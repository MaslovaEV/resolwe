# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-02-28 10:23
from __future__ import unicode_literals

from django.db import migrations

from resolwe.flow.utils import get_data_checksum


def calculate_checksum(apps, schema_editor):
    Data = apps.get_model("flow", "Data")
    for data in Data.objects.all():
        data.checksum = get_data_checksum(data.input, data.process.slug, data.process.version)
        data.save()


class Migration(migrations.Migration):

    dependencies = [
        ('flow', '0022_data_sha1_to_sha256'),
    ]

    operations = [
        migrations.RunPython(calculate_checksum),
    ]
