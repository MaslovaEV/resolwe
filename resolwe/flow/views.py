""".. Ignore pydocstyle D400.

==========
Flow Views
==========

"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import pkgutil
from importlib import import_module

import six

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.db.models.query import Prefetch
from django.utils._os import upath

from guardian import shortcuts
from rest_framework import exceptions, mixins, permissions, status, viewsets
from rest_framework.decorators import detail_route, list_route
from rest_framework.response import Response

from resolwe.flow.utils import dict_dot, get_data_checksum, iterate_schema
from resolwe.permissions.shortcuts import get_object_perms, get_objects_for_user

from .filters import CollectionFilter, DataFilter, EntityFilter, ProcessFilter
from .managers import manager
from .models import Collection, Data, DescriptorSchema, Entity, Process, Relation, Storage
from .models.entity import PositionInRelation, RelationType
from .serializers import (
    CollectionSerializer, DataSerializer, DescriptorSchemaSerializer, EntitySerializer, PositionInRelationSerializer,
    ProcessSerializer, RelationSerializer, StorageSerializer,
)


def assign_perm(*args, **kwargs):
    """Assign permissions wrapper.

    Call original assign_perms function from django-guardian, but don't
    raise exception if permission is not found

    """
    try:
        shortcuts.assign_perm(*args, **kwargs)
    except Permission.DoesNotExist:
        pass


def remove_perm(*args, **kwargs):
    """Remove permissions wrapper.

    Call original remove_perms function from django-guardian, but don't
    raise exception if permission is not found

    """
    try:
        shortcuts.remove_perm(*args, **kwargs)
    except Permission.DoesNotExist:
        pass


def load_permissions(permissions_name):
    """Look for a fully qualified flow permissions class."""
    try:
        return import_module('{}'.format(permissions_name)).ResolwePermissions
    except AttributeError:
        raise AttributeError("'ResolwePermissions' class not found in {} module.".format(
            permissions_name))
    except ImportError as ex:
        # The permissions module wasn't found. Display a helpful error
        # message listing all possible (built-in) permissions classes.
        permissions_dir = os.path.join(os.path.dirname(upath(__file__)), '..', 'perms')
        permissions_dir = os.path.normpath(permissions_dir)

        try:
            builtin_permissions = [
                name for _, name, _ in pkgutil.iter_modules([permissions_dir]) if name not in [u'tests']]
        except EnvironmentError:
            builtin_permissions = []
        if permissions_name not in ['resolwe.auth.{}'.format(p) for p in builtin_permissions]:
            permissions_reprs = map(repr, sorted(builtin_permissions))
            err_msg = ("{} isn't an available flow permissions class.\n"
                       "Try using 'resolwe.auth.XXX', where XXX is one of:\n"
                       "    {}\n"
                       "Error was: {}".format(permissions_name, ", ".join(permissions_reprs), ex))
            raise ImproperlyConfigured(err_msg)
        else:
            # If there's some other error, this must be an error in Django
            raise


permissions_cls = load_permissions(settings.FLOW_API['PERMISSIONS'])  # pylint: disable=invalid-name


class ResolweCreateModelMixin(mixins.CreateModelMixin):
    """Mixin to support creating new `Resolwe` models.

    Extends `django_rest_framework`'s class `CreateModelMixin` with:

      * append user's id from request to posted data as `contributor`
        key
      * catch `IntegrityError`s, so we can return HTTP status 409
        instead of raising error

    """

    def create(self, request, *args, **kwargs):
        """Create a resource."""
        user = request.user
        if not user.is_authenticated():
            raise exceptions.NotFound

        ds_slug = request.data.get('descriptor_schema', None)
        if ds_slug:
            ds_query = DescriptorSchema.objects.filter(slug=ds_slug)
            ds_query = get_objects_for_user(request.user, 'view_descriptorschema', ds_query)
            try:
                request.data['descriptor_schema'] = ds_query.latest().pk
            except DescriptorSchema.DoesNotExist:
                return Response(
                    {'descriptor_schema': [
                        'Invalid descriptor_schema slug "{}" - object does not exist.'.format(ds_slug)]},
                    status=status.HTTP_400_BAD_REQUEST)

        request.data['contributor'] = user.pk
        try:
            return super(ResolweCreateModelMixin, self).create(request, *args, **kwargs)

        except IntegrityError as ex:
            return Response({u'error': str(ex)}, status=status.HTTP_409_CONFLICT)

    def perform_create(self, serializer):
        """Create a resource."""
        with transaction.atomic():
            instance = serializer.save()

            # Assign all permissions to the object contributor.
            for permission in list(zip(*instance._meta.permissions))[0]:  # pylint: disable=protected-access
                assign_perm(permission, instance.contributor, instance)


class ResolweUpdateModelMixin(mixins.UpdateModelMixin):
    """Mixin to support updating `Resolwe` models.

    Extends `django_rest_framework`'s class `UpdateModelMixin` with:

      * translate `descriptor_schema` field from DescriptorSchema's
        slug to its id and return 400 error Response if it doesn't
        exists

    """

    def update(self, request, *args, **kwargs):
        """Update a resource."""
        ds_slug = request.data.get('descriptor_schema', None)
        if ds_slug:
            ds_query = DescriptorSchema.objects.filter(slug=ds_slug)
            ds_query = get_objects_for_user(request.user, 'view_descriptorschema', ds_query)
            try:
                request.data['descriptor_schema'] = ds_query.latest().pk
            except DescriptorSchema.DoesNotExist:
                return Response(
                    {'descriptor_schema': [
                        'Invalid descriptor_schema slug "{}" - object does not exist.'.format(ds_slug)]},
                    status=status.HTTP_400_BAD_REQUEST)

        return super(ResolweUpdateModelMixin, self).update(request, *args, **kwargs)


class ResolweCreateDataModelMixin(ResolweCreateModelMixin):
    """Mixin to support creating new :class:`Data` objects.

    Extends :class:`ResolweCcreateModelMixin` with:

      * checks if there is exactly 1 collection listed on create
      * checks if user has `add` permission on that collection

    """

    def create(self, request, *args, **kwargs):
        """Create a resource."""
        collections = request.data.get('collections', [])

        # check that user has permissions on all collections that Data
        # object will be added to
        for collection_id in collections:
            try:
                collection = Collection.objects.get(pk=collection_id)
            except Collection.DoesNotExist:
                return Response({'collections': ['Invalid pk "{}" - object does not exist.'.format(collection_id)]},
                                status=status.HTTP_400_BAD_REQUEST)

            if not request.user.has_perm('add_collection', obj=collection):
                if request.user.is_authenticated():
                    raise exceptions.PermissionDenied
                else:
                    raise exceptions.NotFound

        # translate processe's slug to id
        process_slug = request.data.get('process', None)
        process_query = Process.objects.filter(slug=process_slug)
        process_query = get_objects_for_user(request.user, 'view_process', process_query)
        try:
            process = process_query.latest()
        except Process.DoesNotExist:
            return Response({'process': ['Invalid process slug "{}" - object does not exist.'.format(process_slug)]},
                            status=status.HTTP_400_BAD_REQUEST)
        request.data['process'] = process.pk

        # perform "get_or_create" if requested - return existing object
        # if found
        if kwargs.pop('get_or_create', False):
            process_input = request.data.get('input', {})

            # use default values if they are not given
            for field_schema, fields, path in iterate_schema(process_input, process.input_schema):
                if 'default' in field_schema and field_schema['name'] not in fields:
                    dict_dot(process_input, path, field_schema['default'])

            checksum = get_data_checksum(process_input, process.slug, process.version)
            data_qs = Data.objects.filter(
                checksum=checksum,
                process__persistence__in=[Process.PERSISTENCE_CACHED, Process.PERSISTENCE_TEMP],
            )
            data_qs = get_objects_for_user(request.user, 'view_data', data_qs)
            if data_qs.exists():
                data = data_qs.order_by('created').last()
                serializer = self.get_serializer(data)
                return Response(serializer.data)

        # create the objects
        resp = super(ResolweCreateDataModelMixin, self).create(request, *args, **kwargs)

        # run manager
        manager.communicate()

        return resp

    @list_route(methods=[u'post'])
    def get_or_create(self, request, *args, **kwargs):
        """Get ``Data`` object if similar already exists, otherwise create it."""
        kwargs['get_or_create'] = True
        return self.create(request, *args, **kwargs)

    def perform_create(self, serializer):
        """Create a resource."""
        with transaction.atomic():
            instance = serializer.save()

            # Assign all permissions to the object contributor.
            for permission in list(zip(*instance._meta.permissions))[0]:  # pylint: disable=protected-access
                assign_perm(permission, instance.contributor, instance)

            # Assign data object to all specified collections.
            collections = self.request.data.get('collections', [])
            for c in collections:
                collection = Collection.objects.get(pk=c)
                collection.data.add(instance)


class ResolwePermissionsMixin(object):
    """Mixin to support managing `Resolwe` objects' permissions."""

    def _fetch_user(self, query):
        """Get user by ``pk`` or ``username``. Return ``None`` if doesn't exist."""
        user_model = get_user_model()

        user_filter = {'pk': query} if query.isdigit() else {'username': query}
        try:
            return user_model.objects.get(**user_filter)
        except user_model.DoesNotExist:
            raise exceptions.ParseError("User ({}) does not exists.".format(user_filter))

    def _fetch_group(self, query):
        """Get group by ``pk`` or ``name``. Return ``None`` if doesn't exist."""
        group_filter = {'pk': query} if query.isdigit() else {'name': query}
        try:
            return Group.objects.get(**group_filter)
        except Group.DoesNotExist:
            raise exceptions.ParseError("Group ({}) does not exists.".format(group_filter))

    def _update_permission(self, obj, data):
        """Update object permissions."""
        content_type = ContentType.objects.get_for_model(obj)
        full_permissions = list(zip(*obj._meta.permissions))[0]  # pylint: disable=protected-access

        def set_permissions(entity_type, perm_type):
            """Set object permissions."""
            perm_func = assign_perm if perm_type == 'add' else remove_perm
            fetch = self._fetch_user if entity_type == 'users' else self._fetch_group

            for entity_id in data.get(entity_type, {}).get(perm_type, []):
                entity = fetch(entity_id)
                if entity:
                    perms = data[entity_type][perm_type][entity_id]
                    if perms == u'ALL':
                        perms = full_permissions
                    for perm in perms:
                        perm_func('{}_{}'.format(perm.lower(), content_type), entity, obj)

        set_permissions('users', 'add')
        set_permissions('users', 'remove')
        set_permissions('groups', 'add')
        set_permissions('groups', 'remove')

        def set_public_permissions(perm_type):
            """Set public permissions."""
            perm_func = assign_perm if perm_type == 'add' else remove_perm
            user = AnonymousUser()
            perms = data.get('public', {}).get(perm_type, [])
            if perms == u'ALL':
                perms = full_permissions
            for perm in perms:
                perm_func('{}_{}'.format(perm.lower(), content_type), user, obj)

        set_public_permissions('add')
        set_public_permissions('remove')

    def get_serializer_class(self):
        """Augment base serializer class.

        Include permissions information with objects.

        """
        base_class = super(ResolwePermissionsMixin, self).get_serializer_class()

        class SerializerWithPermissions(base_class):
            """Augment serializer class."""

            def to_representation(serializer_self, instance):  # pylint: disable=no-self-argument
                """Object serializer."""
                # TODO: These permissions queries may be expensive. Should we limit or optimize this?
                data = super(SerializerWithPermissions, serializer_self).to_representation(instance)
                data['permissions'] = get_object_perms(instance, self.request.user)
                return data

        return SerializerWithPermissions

    def _filter_owner_permission(self, data):
        """Raise ``PermissionDenied``if ``owner`` found in ``data``."""
        for entity_type in ['users', 'groups']:
            if entity_type in data:
                for perm_type in ['add', 'remove']:
                    if perm_type in data[entity_type]:
                        for entity_id in data[entity_type][perm_type]:
                            for perm in data[entity_type][perm_type][entity_id]:
                                if perm == 'owner':
                                    raise exceptions.PermissionDenied("Only owners can grant/revoke owner permission")

    def _filter_public_permissions(self, data):
        """Raise ``PermissionDenied`` if public permissions are too open."""
        allowed_public_permissions = ['view', 'add', 'download']

        if 'public' in data:
            for perm_type in ['add', 'remove']:
                if perm_type in data['public']:
                    for perm in data['public'][perm_type]:
                        if perm not in allowed_public_permissions:
                            raise exceptions.PermissionDenied("Permissions for public users are too open")

    def _filter_user_permissions(self, data, user_pk):
        """Raise ``PermissionDenied`` if ``data`` includes ``user_pk``."""
        if 'users' in data:
            for perm_type in ['add', 'remove']:
                if perm_type in data['users']:
                    if user_pk in data['users'][perm_type].keys():
                        raise exceptions.PermissionDenied("You cannot change your own permissions")

    @detail_route(methods=['get', 'post'], url_path='permissions')
    def detail_permissions(self, request, pk=None):
        """Get or set permissions API endpoint."""
        obj = self.get_object()

        if request.method == 'POST':
            content_type = ContentType.objects.get_for_model(obj)

            owner_perm = 'owner_{}'.format(content_type)
            if not (request.user.has_perm(owner_perm, obj=obj) or request.user.is_superuser):
                self._filter_owner_permission(request.data)
            self._filter_public_permissions(request.data)
            self._filter_user_permissions(request.data, request.user.pk)

            self._update_permission(obj, request.data)

        return Response(get_object_perms(obj))

    @list_route(methods=['get', 'post'], url_path='permissions')
    def list_permissions(self, request):
        """Batch get or set permissions API endpoint."""
        # TODO: Implement batch get/set permissions
        return Response(status=status.HTTP_501_NOT_IMPLEMENTED)


class ResolweProcessPermissionsMixin(ResolwePermissionsMixin):
    """Process permissions mixin."""

    def _update_permission(self, obj, data):
        """Update collection permissions."""
        super(ResolweProcessPermissionsMixin, self)._update_permission(obj, data)

        if 'collections' in data:
            if 'add' in data['collections']:
                for _id in data['collections']['add']:
                    try:
                        Collection.objects.get(pk=_id).public_processes.add(obj)
                    except Collection.DoesNotExist:
                        pass
            if 'remove' in data['collections']:
                for _id in data['collections']['remove']:
                    try:
                        Collection.objects.get(pk=_id).public_processes.remove(obj)
                    except Collection.DoesNotExist:
                        pass


class ResolweCheckSlugMixin(object):
    """Slug validation."""

    @list_route(methods=[u'get'])
    def slug_exists(self, request):
        """Check if given url slug exists.

        Check if slug given in query parameter ``name`` exists. Return
        ``True`` if slug already exists and ``False`` otherwise.

        """
        if not request.user.is_authenticated():
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        if 'name' not in request.query_params:
            return Response({'error': 'Query parameter `name` must be given.'},
                            status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset()
        slug_name = request.query_params['name']
        return Response(queryset.filter(slug__iexact=slug_name).exists())


class CollectionViewSet(ResolweCreateModelMixin,
                        mixins.RetrieveModelMixin,
                        ResolweUpdateModelMixin,
                        mixins.DestroyModelMixin,
                        mixins.ListModelMixin,
                        ResolwePermissionsMixin,
                        ResolweCheckSlugMixin,
                        viewsets.GenericViewSet):
    """API view for :class:`Collection` objects."""

    queryset = Collection.objects.all().prefetch_related(
        'descriptor_schema',
        'contributor',
        Prefetch('data', queryset=Data.objects.all().order_by('id'))
    )
    serializer_class = CollectionSerializer
    permission_classes = (permissions_cls,)
    filter_class = CollectionFilter
    ordering_fields = ('id', 'created', 'modified', 'name')
    ordering = ('id',)

    @detail_route(methods=[u'post'])
    def add_data(self, request, pk=None):
        """Add data to collection."""
        collection = self.get_object()

        if 'ids' not in request.data:
            return Response({"error": "`ids`parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        missing = []
        for data_id in request.data['ids']:
            if not Data.objects.filter(pk=data_id).exists():
                missing.append(data_id)

        if missing:
            return Response(
                {"error": "Data objects with following ids are missing: {}".format(', '.join(missing))},
                status=status.HTTP_400_BAD_REQUEST)

        for data_id in request.data['ids']:
            collection.data.add(data_id)

        return Response()

    @detail_route(methods=[u'post'])
    def remove_data(self, request, pk=None):
        """Remove data from collection."""
        collection = self.get_object()

        if 'ids' not in request.data:
            return Response({"error": "`ids`parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        for data_id in request.data['ids']:
            collection.data.remove(data_id)

        return Response()


class EntityViewSet(CollectionViewSet):
    """API view for entities."""

    filter_class = EntityFilter
    serializer_class = EntitySerializer

    queryset = Entity.objects.prefetch_related(
        Prefetch('data', queryset=Data.objects.all().order_by('id')),
        'descriptor_schema',
        'contributor'
    ).annotate(
        latest_date=Max('data__modified')
    ).order_by('-latest_date')

    def _check_collection_permissions(self, collection_id, user):
        """Check that collection exists and user has `add` permission."""
        collection_query = Collection.objects.filter(pk=collection_id)
        if not collection_query.exists():
            raise exceptions.ValidationError('Collection id does not exist')

        collection = collection_query.first()
        if not user.has_perm('add_collection', obj=collection):
            if user.is_authenticated():
                raise exceptions.PermissionDenied()
            else:
                raise exceptions.NotFound()

    @detail_route(methods=[u'post'])
    def add_to_collection(self, request, pk=None):
        """Add Entity to a collection."""
        entity = self.get_object()

        if 'ids' not in request.data:
            return Response({"error": "`ids` parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        for collection_id in request.data['ids']:
            self._check_collection_permissions(collection_id, request.user)

        for collection_id in request.data['ids']:
            entity.collections.add(collection_id)

            collection = Collection.objects.get(pk=collection_id)
            for data in entity.data.all():
                collection.data.add(data)

        return Response()

    @detail_route(methods=[u'post'])
    def remove_from_collection(self, request, pk=None):
        """Remove Entity from a collection."""
        entity = self.get_object()

        if 'ids' not in request.data:
            return Response({"error": "`ids` parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        for collection_id in request.data['ids']:
            self._check_collection_permissions(collection_id, request.user)

        for collection_id in request.data['ids']:
            entity.collections.remove(collection_id)

            collection = Collection.objects.get(pk=collection_id)
            for data in entity.data.all():
                collection.data.remove(data)

        return Response()

    @detail_route(methods=[u'post'])
    def add_data(self, request, pk=None):
        """Add data to Entity and it's collection."""
        # add data to entity
        resp = super(EntityViewSet, self).add_data(request, pk)

        # add data to collections in which entity is
        entity = self.get_object()
        for collection in entity.collections.all():
            collection.data.add(*request.data['ids'])

        return resp

    @detail_route(methods=[u'post'])
    def remove_data(self, request, pk=None):
        """Remove Data from Entity and delete it if it is empty."""
        resp = super(EntityViewSet, self).remove_data(request, pk)

        entity = self.get_object()
        if entity.data.count() == 0:
            entity.delete()

        return resp


class RelationViewSet(mixins.CreateModelMixin,
                      mixins.ListModelMixin,
                      mixins.RetrieveModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    """API view for :class:`Relation` objects."""

    queryset = Relation.objects.all().prefetch_related('contributor')
    serializer_class = RelationSerializer
    permission_classes = (permissions.IsAuthenticated,)
    ordering_fields = ('id', 'created', 'modified')
    ordering = ('id',)

    def _filter_queryset(self, queryset):
        """Filter queryset by queryparameters.

        Filtering is supported by ``name``, ``collection``, ``entities``
        and ``positions``.

        If ``positions`` parameter is given, it is combined with
        coresponding id in ``samples`` parameter and relations are
        filtered by (sample, position) pairs. Because of this, if
        ``positions`` is given, also ``samples`` must be given and
        they must be of the same length.

        NOTE: Because of complex filtering requirements it is not
              possible to use django_restframework_filters (at least
              not in a straight foreward way)
        """
        # query_params must be casted to dict, otherwise list values cannot be retrieved
        query_params = dict(self.request.query_params)

        id_ = query_params.get('id', None)
        relation_type = query_params.get('type', None)
        label = query_params.get('label', None)
        entities = query_params.get('entity', None)
        positions = query_params.get('position', None)
        collection = query_params.get('collection', None)

        if id_:
            queryset = queryset.filter(id=id_[0])

        if relation_type:
            queryset = queryset.filter(type__name=relation_type[0])

        if label:
            queryset = queryset.filter(label=label[0])

        if positions is not None and (entities is None or len(positions) != len(entities)):
            raise exceptions.ParseError(
                'If `positions` query parameter is given, also `entities` '
                'must be given and they must be of the same length.'
            )

        if collection:
            queryset = queryset.filter(collection__pk=collection[0])

        if entities:
            for entity, position in six.moves.zip_longest(entities, positions or []):
                filter_params = {'entities__pk': entity}
                if position:
                    filter_params['positioninrelation__position'] = position
                queryset = queryset.filter(**filter_params)

        return queryset

    def get_queryset(self):
        """Get queryset and perform custom filtering."""
        return self._filter_queryset(self.queryset)

    def create(self, request, *args, **kwargs):
        """Create a resource."""
        user = request.user
        if not user.is_authenticated():
            raise exceptions.NotFound

        relation_type = request.data.get('type')
        if not relation_type:
            return Response({'type': ['This field is required.']}, status=status.HTTP_400_BAD_REQUEST)

        rel_type_query = RelationType.objects.filter(name=relation_type)
        try:
            request.data['type'] = rel_type_query.last().pk
        except RelationType.DoesNotExist:
            return Response(
                {'type': ['Invalid type name "{}" - object does not exist.'.format(relation_type)]},
                status=status.HTTP_400_BAD_REQUEST)

        request.data['contributor'] = user.pk

        try:
            return super(RelationViewSet, self).create(request, *args, **kwargs)

        except IntegrityError as ex:
            return Response({u'error': str(ex)}, status=status.HTTP_409_CONFLICT)

    def perform_create(self, serializer):
        """Create a relation."""
        with transaction.atomic():
            instance = serializer.save()

            # Assign all permissions to the object contributor.
            for permission in list(zip(*instance._meta.permissions))[0]:  # pylint: disable=protected-access
                assign_perm(permission, instance.contributor, instance)

    @detail_route(methods=[u'post'])
    def add_entity(self, request, pk=None):
        """Add ``Entity`` to ``Relation``."""
        relation = self.get_object()
        serializer = PositionInRelationSerializer(data=request.data, many=True)
        if not serializer.is_valid():
            Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(relation=relation)

        return Response()

    @detail_route(methods=[u'post'])
    def remove_entity(self, request, pk=None):
        """Remove data from collection."""
        if 'ids' not in request.data:
            return Response({"error": "`ids` parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        relation = self.get_object()
        for entity_id in request.data['ids']:
            PositionInRelation.objects.filter(relation=relation, entity=entity_id).delete()

        return Response()


class ProcessViewSet(ResolweCreateModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.ListModelMixin,
                     ResolweProcessPermissionsMixin,
                     ResolweCheckSlugMixin,
                     viewsets.GenericViewSet):
    """API view for :class:`Process` objects."""

    queryset = Process.objects.all().prefetch_related('contributor')
    serializer_class = ProcessSerializer
    permission_classes = (permissions_cls,)
    filter_class = ProcessFilter
    ordering_fields = ('id', 'created', 'modified', 'name', 'version')
    ordering = ('id',)


class DataViewSet(ResolweCreateDataModelMixin,
                  mixins.RetrieveModelMixin,
                  ResolweUpdateModelMixin,
                  mixins.DestroyModelMixin,
                  mixins.ListModelMixin,
                  ResolwePermissionsMixin,
                  ResolweCheckSlugMixin,
                  viewsets.GenericViewSet):
    """API view for :class:`Data` objects."""

    queryset = Data.objects.all().prefetch_related('process', 'descriptor_schema', 'contributor')
    serializer_class = DataSerializer
    permission_classes = (permissions_cls,)
    filter_class = DataFilter
    ordering_fields = ('id', 'created', 'modified', 'started', 'finished', 'name')
    ordering = ('id',)


class DescriptorSchemaViewSet(mixins.RetrieveModelMixin,
                              mixins.ListModelMixin,
                              ResolwePermissionsMixin,
                              viewsets.GenericViewSet):
    """API view for :class:`DescriptorSchema` objects."""

    queryset = DescriptorSchema.objects.all().prefetch_related('contributor')
    serializer_class = DescriptorSchemaSerializer
    permission_classes = (permissions_cls,)
    filter_fields = ('contributor', 'name', 'description', 'created', 'modified', 'slug')
    ordering_fields = ('id', 'created', 'modified', 'name', 'version')
    ordering = ('id',)


class StorageViewSet(mixins.RetrieveModelMixin,
                     mixins.ListModelMixin,
                     viewsets.GenericViewSet):
    """API view for :class:`Storage` objects."""

    queryset = Storage.objects.all().prefetch_related('contributor')
    serializer_class = StorageSerializer
    filter_fields = ('contributor', 'name', 'created', 'modified', 'slug')
