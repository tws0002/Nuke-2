# -*- coding=UTF-8 -*-
"""CGTeamWork pyblish plug-in.  """

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import os
import sys
from collections import namedtuple

import nuke
import pendulum
import pyblish.api

import callback
from node import wlf_write_node
from nuketools import keep_modifield_status
from wlf.codectools import get_unicode as u
from wlf.fileutil import copy

FootageInfo = namedtuple('FootageInfo', ('filename', 'mtime'))

# pylint: disable=no-init


class CollectFile(pyblish.api.ContextPlugin):
    """获取当前Nuke使用的文件.   """

    order = pyblish.api.CollectorOrder
    label = '获取当前文件'

    def process(self, context):
        context.data['comment'] = ''
        assert isinstance(context, pyblish.api.Context)
        filename = nuke.value('root.name')
        if not filename:
            raise ValueError('工程尚未保存.')

        context.create_instance(filename,
                                family='Nuke文件')


class CollectFrameRange(pyblish.api.ContextPlugin):
    """获取当前工程帧范围设置.   """

    order = pyblish.api.CollectorOrder
    label = '获取帧范围'

    def process(self, context):
        first = nuke.numvalue('root.first_frame')
        last = nuke.numvalue('root.last_frame')
        context.create_instance(
            '帧范围: {:.0f}-{:.0f}'.format(first, last),
            first=first,
            last=last,
            family='帧范围')


class CollectFPS(pyblish.api.ContextPlugin):
    """获取当前工程帧速率设置.   """

    order = pyblish.api.CollectorOrder
    label = '获取帧速率'

    def process(self, context):
        fps = nuke.numvalue('root.fps')
        context.create_instance(
            name='帧速率: {:.0f}'.format(fps),
            fps=fps,
            family='帧速率')


class CollectMTime(pyblish.api.ContextPlugin):
    """获取当前工程使用的素材.   """

    order = pyblish.api.CollectorOrder
    label = '获取素材'

    def process(self, context):
        assert isinstance(context, pyblish.api.Context)
        footages = set()
        root = nuke.Root()
        for n in nuke.allNodes('Read', nuke.Root()):
            if n.hasError():
                self.log.warning('读取节点出错: %s', n.name())
                continue
            filename = nuke.filename(n)
            mtime = n.metadata('input/mtime')
            if not filename or not mtime:
                continue

            footage = FootageInfo(filename=filename,
                                  mtime=pendulum.from_format(
                                      mtime,
                                      '%Y-%m-%d %H:%M:%S',
                                      tz='Asia/Shanghai'))
            footages.add(footage)
        instance = context.create_instance(
            '{}个 素材'.format(len(footages)),
            filename=root['name'].value(),
            family='素材')
        instance.extend(footages)


class CollectMemoryUsage(pyblish.api.ContextPlugin):
    """获取当前工程内存占用.   """

    order = pyblish.api.CollectorOrder
    label = '获取内存占用'

    def process(self, context):
        assert isinstance(context, pyblish.api.Context)
        number = nuke.memory('usage')

        context.create_instance(
            '内存占用: {}GB'.format(round(number / 2.0 ** 30, 2)),
            number=number,
            family='内存')


class ValidateFootageStore(pyblish.api.InstancePlugin):
    """检查素材文件是否保存于服务器.  """

    order = pyblish.api.ValidatorOrder
    label = '检查素材保存位置'
    families = ['素材']
    valid_dir = ('x:\\', 'y:\\', 'z:\\') if sys.platform == 'win32' else ('',)

    def process(self, instance):
        is_ok = True
        for i in instance:
            assert isinstance(i, FootageInfo)
            if not os.path.normcase(u(i.filename)).startswith(self.valid_dir):
                self.log.error('使用了本地素材: %s', i.filename)
                is_ok = False
        if not is_ok:
            raise ValueError('Local file used.')


class ExtractJPG(pyblish.api.InstancePlugin):
    """生成单帧图.   """

    order = pyblish.api.ExtractorOrder
    label = '生成JPG'
    families = ['Nuke文件']

    def process(self, instance):
        with keep_modifield_status():
            if not nuke.numvalue('preferences.wlf_render_jpg', 0.0):
                self.log.info('因首选项而跳过生成JPG')
                return

            n = wlf_write_node()
            if n:
                self.log.debug('render_jpg: %s', n.name())
                try:
                    n['bt_render_JPG'].execute()
                except RuntimeError as ex:
                    nuke.message(str(ex))
            else:
                self.log.warning('工程中缺少wlf_Write节点')


class SendToRenderDir(pyblish.api.InstancePlugin):
    """发送Nuke文件至渲染文件夹.   """

    order = pyblish.api.IntegratorOrder
    label = '发送至渲染文件夹'
    families = ['Nuke文件']

    def process(self, instance):
        filename = instance.data['name']
        if nuke.numvalue('preferences.wlf_send_to_dir', 0.0):
            render_dir = u(nuke.value('preferences.wlf_render_dir'))
            copy(filename, render_dir + '/')
        else:
            self.log.info('因为首选项设置而跳过')
