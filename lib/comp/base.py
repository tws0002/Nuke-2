# -*- coding=UTF-8 -*-
"""Gather footages and create output.  """

from __future__ import absolute_import, print_function, unicode_literals

import logging
import os
import re
import webbrowser

import nuke

from comp.config import BatchCompConfig, CompConfig
from comp.precomp import Precomp
from node import ReadNode
from nuketools import undoable_func, utf8, utf8_dict
from orgnize import autoplace
from wlf.codectools import get_encoded as e
from wlf.codectools import get_unicode as u
from wlf.progress.core import DefaultHandler

CONFIG = CompConfig()
LOGGER = logging.getLogger('com.wlf.comp')
BATCH_CONFIG = BatchCompConfig()


class Comp(object):
    """Create .nk file from footage that taged in filename."""

    tag_metadata_key = 'comp/tag'
    _attenuation_radial = None

    def __init__(self):
        self._errors = []
        self._bg_ch_end_nodes = []
        self._task = DefaultHandler()
        self._task.total = 20
        self._task.on_started()

        for key, value in CONFIG.iteritems():
            if isinstance(value, str):
                CONFIG[key] = value.replace('\\', '/')

    @property
    def fps(self):
        """Frame per secondes.  """
        return CONFIG.get('fps') or nuke.numvalue('root.fps')

    def task_step(self, message=None):
        """Push task progress bar forward.  """

        if message:
            LOGGER.info('{:-^30s}'.format(message))
        self._task.step(message)

    def import_resource(self, dir_path):
        """Import footages from config dictionary."""

        # Get all subdir
        dirs = list(x[0] for x in os.walk(dir_path))
        self.task_step('导入素材')
        for dir_ in dirs:
            # Get footage in subdir
            LOGGER.info('文件夹 %s:', dir_)
            if not re.match(BATCH_CONFIG['dir_pat'], os.path.basename(dir_.rstrip('\\/'))):
                LOGGER.info('\t不匹配文件夹正则, 跳过')
                continue

            files = nuke.getFileNameList(utf8(dir_))
            footages = [u(i) for i in files if
                        not u(i).endswith(('副本', '.lock'))] if files else []
            if footages:
                for f in footages:
                    if os.path.isdir(e(os.path.join(dir_, f))):
                        LOGGER.info('\t文件夹: %s', f)
                        continue
                    LOGGER.info('\t素材: %s', f)
                    if re.match(CONFIG['footage_pat'], f, flags=re.I):
                        n = nuke.createNode(
                            'Read', utf8('file {{{}/{}}}'.format(dir_, f)))
                        n['on_error'].setValue(utf8('nearest frame'))
                    else:
                        LOGGER.info('\t\t不匹配素材正则, 跳过')
        LOGGER.info('{:-^30s}'.format('结束 导入素材'))

        if not nuke.allNodes('Read'):
            raise FootageError(dir_path)

    def setup(self):
        """Add tag knob to read nodes, then set project framerange."""

        if not nuke.value('root.project_directory'):
            nuke.knob("root.project_directory",
                      r"[python {os.path.join("
                      r"nuke.value('root.name', ''), '../'"
                      r").replace('\\', '/')}]")

        nodes = nuke.allNodes('Read')
        if not nodes:
            raise FootageError('没有读取节点')

        n = None
        root_format = None
        root = nuke.Root()
        first = None
        last = None
        formats = []

        for n in nodes:
            ReadNode(n)
            format_ = n.format()
            assert isinstance(format_, nuke.Format)
            if not n.hasError():
                formats.append((format_.width(), format_.height()))
            n_first, n_last = n.firstFrame(), n.lastFrame()

            # Ignore single frame.
            if n_first == n_last:
                continue

            # Expand frame range.
            if first is None:
                first = n_first
                last = n_last
            else:
                first = min(last, n.firstFrame())
                last = max(last, n.lastFrame())

        if first is None:
            first = last = 1

        root_format = root_format or n.format()

        root['first_frame'].setValue(first)
        root['last_frame'].setValue(last)
        nuke.frame((first + last) / 2)
        root['lock_range'].setValue(True)
        try:
            format_ = sorted([(formats.count(i), i)
                              for i in set(formats)])[-1][1]
            root['format'].setValue(nuke.addFormat('{} {}'.format(*format_)))
        except IndexError:
            root['format'].setValue('HD_1080')
            LOGGER.warning('不能获取工程格式, 使用默认值HD_1080')

        root['fps'].setValue(self.fps)

    def _attenuation_adjust(self, input_node):
        n = input_node
        if self._attenuation_radial is None:
            radial_node = nuke.nodes.Radial(
                area='0 0 {} {}'.format(n.width(), n.height()))
            self._attenuation_radial = radial_node
        else:
            radial_node = nuke.nodes.Dot(
                inputs=[self._attenuation_radial], hide_input=True)

        n = nuke.nodes.Merge2(
            inputs=[n, radial_node],
            operation='soft-light',
            output='rgb',
            mix='0.6',
            label=b'衰减调整',
            disable=True)

        return n

    @undoable_func('自动合成')
    def create_nodes(self):
        """Create nodes that a comp need."""

        self.task_step('分析读取节点')
        self.setup()
        self.task_step('创建节点树')

        self.task_step('合并BG CH')
        n = self._bg_ch_nodes()

        if CONFIG['other']:
            self.task_step('合并其他层')
            n = self._merge_other(n, self._bg_ch_end_nodes)

        if CONFIG['depth']:
            self.task_step('创建整体深度')
            nodes = nuke.allNodes('DepthFix')
            n = self._merge_depth(n, nodes)

        if CONFIG['zdefocus']:
            self.task_step('添加虚焦控制')
            try:
                self._add_zdefocus_control(n)
            except RuntimeError as ex:
                LOGGER.error(u(ex))

        n = nuke.nodes.Unpremult(inputs=[n], label=utf8('整体调色开始'))

        n = nuke.nodes.Premult(inputs=[n], label=utf8('整体调色结束'))

        n = nuke.nodes.Reformat(
            inputs=[n],
            resize='none',
            label=utf8('整体滤镜开始'))

        n = nuke.nodes.SoftClip(
            inputs=[n], conversion='logarithmic compress')
        if CONFIG.get('filters'):
            n = nuke.nodes.Glow2(inputs=[n],
                                 tolerance=0.6,
                                 saturation=0.5,
                                 size=100,
                                 mix=0.25,
                                 disable=True)
            if 'motion' in nuke.layers(n):
                n = nuke.nodes.VectorBlur2(
                    inputs=[n], uv='motion', scale=1,
                    soft_lines=True, normalize=False, disable=True)
        n = nuke.nodes.Aberration(
            inputs=[n], distortion1='0 0 0.003', label=utf8('溢色'))

        n = nuke.nodes.Reformat(
            inputs=[n],
            resize='none',
            label=utf8('整体滤镜结束'))

        n = nuke.nodes.wlf_Write(inputs=[n])
        n.setName('_Write')
        self.task_step('输出节点创建')

        self.task_step('设置查看器')
        map(nuke.delete, nuke.allNodes('Viewer'))
        nuke.nodes.Viewer(inputs=[n, n.input(0), n, n])

        autoplace(undoable=False)

    @staticmethod
    def _merge_mp(input_node, mp_file='', lut=''):
        def _add_lut(input_node):
            if not lut:
                return input_node

            n = nuke.nodes.Vectorfield(
                inputs=[input_node],
                file_type='vf',
                label='[basename [value this.knob.vfield_file]]')
            n['vfield_file'].fromUserText(lut)
            return n

        mp_file = mp_file.replace('\\', '/')

        if mp_file.endswith('.nk'):
            n = nuke.nodes.Precomp(file=mp_file,
                                   postage_stamp=True)
        else:
            n = nuke.nodes.Read(file=mp_file)
            n['file'].fromUserText(mp_file)
        n.setName('MP')
        n = nuke.nodes.ModifyMetaData(
            inputs=[n],
            label=b'元数据标签',
            metadata='{{set comp/tag {}}}'.format('MP'))

        n = nuke.nodes.Reformat(inputs=[n], resize='fill')
        n = nuke.nodes.Transform(inputs=[n])

        n = nuke.nodes.Unpremult(inputs=[n], label=b'调色开始')
        n = _add_lut(n)

        if CONFIG['colorcorrect']:
            n = nuke.nodes.Grade(inputs=[n], disable=True)
            n = nuke.nodes.Grade(
                inputs=[n, nuke.nodes.Ramp(p0='1700 1000', p1='1700 500')],
                disable=True)
        n = nuke.nodes.Premult(inputs=[n], label=b'调色结束')

        n = nuke.nodes.ProjectionMP(inputs=[n])
        n = nuke.nodes.SoftClip(
            inputs=[n], conversion='logarithmic compress')
        n = nuke.nodes.Reformat(inputs=[n], resize='none')
        n = nuke.nodes.DiskCache(inputs=[n])
        input_node = nuke.nodes.wlf_Lightwrap(inputs=[input_node, n],
                                              label=b'MP灯光包裹')
        n = nuke.nodes.Merge2(
            inputs=[input_node, n], operation='under', bbox='B', label='MP')

        return n

    @classmethod
    def _nodes_order(cls, n):
        tag = n.metadata(
            cls.tag_metadata_key) or n[ReadNode.tag_knob_name].value()
        return ('_' + tag.replace('_BG', '1_').replace('_CH', '0_'))

    @classmethod
    def get_nodes_by_tags(cls, tags):
        """Return nodes that match given tags."""
        ret = []
        if isinstance(tags, (str, unicode)):
            tags = [tags]
        tags = tuple(unicode(i).upper() for i in tags)

        for n in nuke.allNodes('Read'):
            knob_name = '{}.{}'.format(n.name(), ReadNode.tag_knob_name)
            tag = nuke.value(knob_name, '')
            if tag.partition('_')[0] in tags:
                ret.append(n)

        ret.sort(key=cls._nodes_order, reverse=True)
        return ret

    def output(self, filename):
        """Save .nk file and render .jpg file."""

        LOGGER.info('{:-^30s}'.format('开始 输出'))
        _path = filename.replace('\\', '/')
        _dir = os.path.dirname(_path)
        if not os.path.exists(_dir):
            os.makedirs(_dir)

        # Save nk
        LOGGER.info('保存为:\t%s', _path)
        nuke.Root()['name'].setValue(_path)
        nuke.scriptSave(_path)

        # Render png
        if CONFIG.get('RENDER_JPG'):
            for n in nuke.allNodes('Read'):
                name = n.name()
                if name in ('MP', 'Read_Write_JPG'):
                    continue
                for frame in (n.firstFrame(), n.lastFrame(),
                              int(nuke.numvalue('_Write.knob.frame'))):
                    try:
                        render_png(n, frame)
                        break
                    except RuntimeError:
                        continue

        # Render Single Frame
        n = nuke.toNode('_Write')
        if n:
            n = n.node('Write_JPG_1')
            n['disable'].setValue(False)
            for frame in (int(nuke.numvalue('_Write.knob.frame')), n.firstFrame(), n.lastFrame()):
                try:
                    nuke.execute(n, frame, frame)
                    break
                except RuntimeError:
                    continue
            else:
                self._errors.append(
                    '{}:\t渲染出错'.format(os.path.basename(_path)))
                raise RenderError('Write_JPG_1')
            LOGGER.info('{:-^30s}'.format('结束 输出'))

    def _bg_ch_nodes(self):
        nodes = self._precomp()

        if not nodes:
            raise FootageError('BG', 'CH')

        for i, n in enumerate(nodes):
            self.task_step('创建{}'.format(
                n.metadata(self.tag_metadata_key) or n.name()))
            n = self._prepare_channels(n)
            n = self._add_colorcorrect_nodes(n)

            if i == 0:
                self.task_step('创建MP')
                n = self._merge_mp(
                    n, mp_file=CONFIG['mp'], lut=CONFIG.get('mp_lut'))
                n = self._merge_occ(n)
                n = self._merge_shadow(n)
                n = self._merge_screen(n)

            n = self._add_filter_nodes(n)

            if i == 0:
                n = nuke.nodes.ModifyMetaData(
                    inputs=[n], metadata='{{set {} main}}'.format(
                        self.tag_metadata_key),
                    label=utf8('主干开始'))

            if i > 0:
                n = nuke.nodes.Merge2(
                    inputs=[nodes[i - 1], n],
                    label=n.metadata(self.tag_metadata_key) or ''
                )

            nodes[i] = n
        return n

    def _precomp(self):
        tag_nodes_dict = {}
        ret = []

        def _tag_order(tag):
            return ('_' + tag.replace('_BG', '1_').replace('_CH', '0_'))

        for n in self.get_nodes_by_tags(['BG', 'CH']):
            tag = n[ReadNode.tag_knob_name].value()
            tag_nodes_dict.setdefault(tag, [])
            tag_nodes_dict[tag].append(n)

        tags = sorted(tag_nodes_dict.keys(), key=_tag_order)
        for tag in tags:
            nodes = tag_nodes_dict[tag]
            try:
                LOGGER.debug('Precomp: %s', tag)
                if len(nodes) == 1 and not CONFIG.get('precomp'):
                    n = nodes[0]
                else:
                    n = Precomp.redshift(nodes, async_=False)
                n = nuke.nodes.ModifyMetaData(
                    inputs=[n], metadata='{{set {} {}}}'.format(
                        self.tag_metadata_key, tag),
                    label=utf8('元数据标签'))
                ret.append(n)
            except AssertionError:
                ret.extend(nodes)

        return ret

    @classmethod
    def _prepare_channels(cls, input_node):
        n = input_node
        if CONFIG['masks']:
            if 'SSS.alpha' in input_node.channels():
                n = nuke.nodes.Keyer(
                    inputs=[n],
                    input='SSS',
                    output='SSS.alpha',
                    operation='luminance key',
                    range='0 0.01 1 1'
                )
            if 'Emission.alpha' in input_node.channels():
                n = nuke.nodes.Keyer(
                    inputs=[n],
                    input='Emission',
                    output='Emission.alpha',
                    operation='luminance key',
                    range='0 0.2 1 1'
                )
        if 'MotionVectors' in nuke.layers(input_node):
            n = nuke.nodes.MotionFix(
                inputs=[n], channel='MotionVectors', output='motion')
        if 'depth.Z' not in input_node.channels():
            _constant = nuke.nodes.Constant(
                channels='depth',
                color=1,
                label=b'**用渲染出的depth层替换这个**\n或者手动指定数值'
            )
            n = nuke.nodes.Merge2(
                inputs=[n, _constant],
                also_merge='all',
                label='add_depth'
            )
        n = nuke.nodes.Reformat(inputs=[n], resize='fit')

        n = nuke.nodes.DepthFix(inputs=[n])
        return n

    def _add_colorcorrect_nodes(self, input_node):

        tag = input_node.metadata(self.tag_metadata_key)
        n = input_node

        # if CONFIG['autograde']:
        #     if get_max(input_node, 'depth.Z') > 1.1:
        #         n['farpoint'].setValue(10000)

        n = nuke.nodes.Unpremult(inputs=[n], label=b'调色开始')

        # if CONFIG['autograde']:
        #     LOGGER.info('{:-^30s}'.format('开始 自动亮度'))
        #     n = nuke.nodes.Grade(
        #         inputs=[n],
        #         unpremult='rgba.alpha',
        #         label='白点: [value this.whitepoint]\n混合:[value this.mix]\n使亮度范围靠近0-1'
        #     )
        #     _max = self._autograde_get_max(input_node)
        #     n['whitepoint'].setValue(_max)
        #     n['mix'].setValue(0.3 if _max < 0.5 else 0.6)
        #     LOGGER.info('{:-^30s}'.format('结束 自动亮度'))
        if CONFIG['colorcorrect']:
            n = nuke.nodes.ColorCorrect(inputs=[n], disable=True)
            n = nuke.nodes.RolloffContrast(
                inputs=[n], channels='rgb',
                contrast=2, center=0.001, soft_clip=1, disable=True)

            if tag and tag.startswith('BG'):
                kwargs = {'in': 'depth', 'label': '远处'}
                input_mask = nuke.nodes.PositionKeyer(
                    inputs=[n], **utf8_dict(kwargs))
                n = nuke.nodes.ColorCorrect(
                    inputs=[n, input_mask], disable=True)
                n = nuke.nodes.Grade(
                    inputs=[n, input_mask],
                    black=0.05,
                    black_panelDropped=True,
                    label=utf8('深度雾'),
                    disable=True)
                kwargs = {'in': 'depth', 'label': '近处'}
                input_mask = nuke.nodes.PositionKeyer(
                    inputs=[n], **utf8_dict(kwargs))
                n = nuke.nodes.ColorCorrect(
                    inputs=[n, input_mask], disable=True)
                n = nuke.nodes.RolloffContrast(
                    inputs=[n, input_mask],
                    channels='rgb',
                    contrast=2,
                    center=0.001,
                    soft_clip=1,
                    disable=True)
            n = self._attenuation_adjust(n)

        n = nuke.nodes.Premult(inputs=[n], label=b'调色结束')

        return n

    def _add_filter_nodes(self, input_node):
        n = nuke.nodes.Reformat(
            inputs=[input_node],
            resize='none',
            label=b'滤镜开始')

        n = nuke.nodes.SoftClip(
            inputs=[n], conversion='logarithmic compress')

        if CONFIG['zdefocus']:
            try:
                n = nuke.nodes.ZDefocus2(
                    inputs=[n],
                    math='depth',
                    center='{{[value _ZDefocus.center curve]}}',
                    focal_point='1.#INF 1.#INF',
                    dof='{{[value _ZDefocus.dof curve]}}',
                    blur_dof='{{[value _ZDefocus.blur_dof curve]}}',
                    size='{{[value _ZDefocus.size curve]}}',
                    max_size='{{[value _ZDefocus.max_size curve]}}',
                    label=b'[\nset trg parent._ZDefocus\n'
                    b'knob this.math [value $trg.math depth]\n'
                    b'knob this.z_channel [value $trg.z_channel depth.Z]\n'
                    b'if {[exists _ZDefocus]} '
                    b'{return \"由_ZDefocus控制\"} '
                    b'else '
                    b'{return \"需要_ZDefocus节点\"}\n]',
                    disable='{{![exists _ZDefocus] '
                    '|| [if {[value _ZDefocus.focal_point \"200 200\"] == \"200 200\" '
                    '|| [value _ZDefocus.disable]} {return True} else {return False}]}}'
                )
                if not (n.metadata(self.tag_metadata_key) or '').startswith('BG'):
                    n['autoLayerSpacing'].setValue(False)
                    n['layers'].setValue(5)
            except RuntimeError as ex:
                LOGGER.error(u(ex))

        if CONFIG['filters']:
            if 'motion' in nuke.layers(n):
                n = nuke.nodes.VectorBlur2(
                    inputs=[n], uv='motion', scale=1, soft_lines=True, normalize=True, disable=True)

            try:
                n = nuke.nodes.RSMB(
                    inputs=[n], disable=True, label=b'运动模糊')
            except RuntimeError:
                LOGGER.info('RSMB插件未安装')

            if 'Emission' in nuke.layers(n):
                kwargs = {'W': 'Emission.alpha'}
            else:
                kwargs = {'disable': True}
            n = nuke.nodes.Glow2(
                inputs=[n], size=30, label=b'自发光辉光', **kwargs)

        n = nuke.nodes.Reformat(
            inputs=[n],
            resize='none',
            label=b'滤镜结束')

        n = nuke.nodes.DiskCache(inputs=[n])

        self._bg_ch_end_nodes.append(n)
        return n

    # @staticmethod
    # def _autograde_get_max(n):
    #     # Exclude small highlight
    #     ret = 100
    #     erode = 0
    #     n = nuke.nodes.Dilate(inputs=[n])
    #     while ret > 1 and erode > n.height() / -100.0:
    #         n['size'].setValue(erode)
    #         LOGGER.info('收边 %s', erode)
    #         ret = get_max(n, 'rgb')
    #         erode -= 1
    #     nuke.delete(n)

    #     return ret

    @staticmethod
    def _merge_depth(input_node, nodes):
        if len(nodes) < 2:
            return input_node

        merge_node = nuke.nodes.Merge2(
            inputs=nodes[:2] + [None] + nodes[2:],
            tile_color=2184871423,
            operation='min',
            Achannels='depth', Bchannels='depth', output='depth',
            label=b'整体深度',
            hide_input=True)
        copy_node = nuke.nodes.Copy(
            inputs=[input_node, merge_node], from0='depth.Z', to0='depth.Z')
        return copy_node

    @classmethod
    def _merge_other(cls, input_node, nodes):
        nodes.sort(key=cls._nodes_order)
        if not nodes:
            return input_node
        input0 = input_node
        n = None
        for n in nodes:
            n = nuke.nodes.Dot(inputs=[n], hide_input=True)
            n = nuke.nodes.Grade(inputs=[n],
                                 channels='alpha',
                                 blackpoint='0.99',
                                 label=b'去除抗锯齿部分的内容')
            n = nuke.nodes.Merge2(
                inputs=[input0, n, n],
                operation='copy',
                also_merge='all',
                label=n.metadata(cls.tag_metadata_key) or '')
            input0 = n
        n = nuke.nodes.Remove(inputs=[n],
                              channels='rgba')
        n = nuke.nodes.Merge2(
            inputs=[input_node, n],
            operation='copy',
            Achannels='none', Bchannels='none', output='none',
            also_merge='all',
            label=b'合并rgba以外的层'
        )
        return n

    @staticmethod
    def _merge_occ(input_node):
        _nodes = Comp.get_nodes_by_tags(('OC', 'OCC', 'AO'))
        n = input_node
        for _read_node in _nodes:
            _reformat_node = nuke.nodes.Reformat(
                inputs=[_read_node], resize='fit')
            n = nuke.nodes.Merge2(
                inputs=[n, _reformat_node],
                output='rgb',
                operation='multiply',
                screen_alpha=True,
                label='OCC'
            )
        return n

    @staticmethod
    def _merge_shadow(input_node):
        _nodes = Comp.get_nodes_by_tags(['SH', 'SD'])
        n = input_node
        for _read_node in _nodes:
            _reformat_node = nuke.nodes.Reformat(
                inputs=[_read_node], resize='fit')
            n = Comp._grade_shadow([n, _reformat_node])
        return n

    @staticmethod
    def _grade_shadow(inputs):
        n = nuke.nodes.Grade(
            inputs=inputs,
            white="0.08420000225 0.1441999972 0.2041999996 0.0700000003",
            white_panelDropped=True,
            label='Shadow'
        )
        return n

    @staticmethod
    def _merge_occsh(input_node):
        nodes = Comp.get_nodes_by_tags(('OCCSH',))
        n = input_node
        for read_node in nodes:
            input1 = nuke.nodes.Reformat(
                inputs=[read_node], resize='fit')
            n = Comp._grade_shadow([n, input1])
            input1 = nuke.nodes.Invert(inputs=[input1])
            n = nuke.nodes.Merge2(
                inputs=[n, input1],
                channels='rgb',
                operation='multiply',
                screen_alpha=True,
                label='OCC'
            )
        return n

    @classmethod
    def _merge_screen(cls, input_node):
        _nodes = Comp.get_nodes_by_tags('FOG')
        n = input_node
        for _read_node in _nodes:
            input1 = nuke.nodes.Reformat(
                inputs=[_read_node], resize='fit')
            if 'VolumeLighting' in nuke.layers(input1):
                input1 = nuke.nodes.Shuffle(
                    inputs=[input1],
                    ** {'in': 'VolumeLighting'})
            n = nuke.nodes.Merge2(
                inputs=[n, input1],
                output='rgb',
                operation='screen',
                maskChannelInput='rgba.alpha',
                label=_read_node[ReadNode.tag_knob_name].value(),
            )
        return n

    @staticmethod
    def _add_zdefocus_control(input_node):
        # Use for one-node zdefocus control
        n = nuke.nodes.ZDefocus2(
            inputs=[input_node], math='depth', output='focal plane setup',
            center=0.00234567, blur_dof=False, label=utf8('** 虚焦总控制 **\n在此拖点定虚焦及设置'))
        n.setName('_ZDefocus')
        return n


def render_png(nodes, frame=None, show=False):
    """create png for given @nodes."""

    assert isinstance(nodes, (nuke.Node, list, tuple))
    assert nuke.value('root.project_directory'), '未设置工程目录'
    if isinstance(nodes, nuke.Node):
        nodes = (nodes,)
    script_name = os.path.join(os.path.splitext(
        os.path.basename(nuke.value('root.name')))[0])
    if frame is None:
        frame = nuke.frame()
    for read_node in nodes:
        if read_node.hasError() or read_node['disable'].value():
            continue
        name = read_node.name()
        LOGGER.info('渲染: %s', name)
        n = nuke.nodes.Write(
            inputs=[read_node], channels='rgba')
        n['file'].fromUserText(os.path.join(
            script_name, '{}.{}.png'.format(name, frame)))
        nuke.execute(n, frame, frame)

        nuke.delete(n)
    if show:
        webbrowser.open(os.path.join(nuke.value(
            'root.project_directory'), script_name))


class FootageError(Exception):
    """Indicate not found needed footage."""

    def __init__(self, *args):
        super(FootageError, self).__init__()
        self.tags = args

    def __str__(self):
        return ' # '.join(self.tags)


class RenderError(Exception):
    """Indicate some problem caused when rendering."""

    def __init__(self, *args):
        super(RenderError, self).__init__()
        self.tags = args

    def __str__(self):
        return ' # '.join(self.tags)
