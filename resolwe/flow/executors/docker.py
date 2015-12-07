"""Local workflow executor"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import shlex
import subprocess

from .local import FlowExecutor as LocalFlowExecutor


class FlowExecutor(LocalFlowExecutor):
    def start(self):
        self.proc = subprocess.Popen(
            shlex.split('docker run --rm --interactive --name={} centos /bin/bash'.format(str(self.data_id))),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

        self.stdout = self.proc.stdout

    def run_script(self, script):
        self.proc.stdin.write(os.linesep.join(['set -x', 'set +B', script]) + os.linesep)
        self.proc.stdin.close()

    def end(self):
        self.proc.wait()

        return self.proc.returncode

    def terminate(self, data_id):
        subprocess.call(shlex.split('docker rm -f {}').format(str(data_id)))