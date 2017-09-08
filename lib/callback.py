# -*- coding: UTF-8 -*-
"""Add callback for wlf plugins."""

import os

import nuke
import nukescripts

import asset
import edit
import _ui
import cgtwn
import orgnize

from wlf import csheet
from wlf.notify import Progress

from node import wlf_write_node, Last


def init():
    """Add callback for nuke init phase."""

    nuke.addBeforeRender(create_out_dirs, nodeClass='Write')
    print(u'启用渲染前自动生成文件夹')


def menu():
    """Add callback for nuke menu phase."""

    nuke.addOnScriptSave(_autoplace)
    nuke.addOnScriptSave(_enable_node)
    nuke.addOnScriptSave(_lock_connections)
    nuke.addOnScriptSave(_jump_frame)

    nuke.addOnScriptClose(_send_to_render_dir)
    nuke.addOnScriptClose(_render_jpg)
    nuke.addOnScriptClose(_create_csheet)

    nuke.addUpdateUI(_gizmo_to_group_update_ui)
    nuke.addOnUserCreate(_gizmo_to_group_on_create)

    print(u'增强节点标签')
    nuke.addAutolabel(_ui.custom_autolabel)

    print(u'增强文件拖放')
    nukescripts.addDropDataCallback(asset.dropdata_handler)

    print(u'启用缺帧检查')
    nuke.addOnScriptLoad(asset.DropFrames.check)
    nuke.addOnScriptSave(asset.DropFrames.show)

    print(u'随机节点控制器颜色')
    nuke.addOnCreate(lambda: edit.set_random_glcolor(nuke.thisNode()))

    print(u'启用自动工程设置')
    nuke.addOnScriptLoad(_add_root_info)
    nuke.addOnScriptLoad(_eval_proj_dir)
    nuke.addOnScriptLoad(Last.on_load_callback)
    nuke.addOnScriptSave(_check_project)
    nuke.addOnScriptSave(_check_fps)

    if cgtwn.cgtwq.MODULE_ENABLE:
        print(u'启用CGTeamWork集成')
        nuke.addOnScriptLoad(cgtwn.on_load_callback)
        nuke.addOnScriptSave(cgtwn.on_save_callback)
        nuke.addOnScriptClose(cgtwn.on_close_callback)


def abort_modified(func):
    """(Decorator)Abort function when project has been modified."""

    def _func():
        if nuke.Root().modified():
            return False
        func()
    return _func


def _enable_node():
    if nuke.numvalue('preferences.wlf_enable_node', 0.0):
        enable_node('_enable_')


def enable_node(prefix='_'):
    """Enable all nodes with given prefix."""
    task = Progress('启用节点')
    nodes = tuple(n for n in nuke.allNodes() if n.name().startswith(prefix))

    total = len(nodes)
    for index, n in enumerate(nodes):
        task.set(index * 100 // total, n.name())
        n['disable'].setValue(False)


@abort_modified
def _create_csheet():
    if nuke.numvalue('preferences.wlf_create_csheet', 0.0):
        if nuke.value('root.name'):
            csheet.create_html_from_dir(os.path.join(
                nuke.value('root.project_directory'), 'images'))


def _eval_proj_dir():
    if nuke.numvalue('preferences.wlf_eval_proj_dir', 0.0):
        attr = 'root.project_directory'
        nuke.knob(attr, os.path.abspath(nuke.value(attr)).replace('\\', '/'))


def _check_project():
    project_directory = nuke.value('root.project_directory')
    if not project_directory:
        _name = nuke.value('root.name', '')
        if _name:
            _dir = os.path.dirname(_name)
            nuke.knob('root.project_directory', _dir)
            nuke.message('工程目录未设置, 已自动设为: {}'.format(_dir))
        else:
            nuke.message('工程目录未设置')
    # avoid ValueError of script_directory() when no root.name.
    elif project_directory == r"[python {os.path.abspath(os.path.join("\
        r"'D:/temp', nuke.value('root.name', ''), '../'"\
            r")).replace('\\', '/')}]":
        nuke.knob('root.project_directory',
                  r"[python {os.path.join("
                  r"nuke.value('root.name', ''), '../'"
                  r").replace('\\', '/')}]")


def _check_fps():
    default_fps = cgtwn.CurrentShot.get_info().get('fps', 30)
    fps = nuke.numvalue('root.fps')

    if fps != default_fps:
        confirm = nuke.ask('当前fps: {}, 设为默认值: {} ?'.format(fps, default_fps))
        if confirm:
            nuke.knob('root.fps', str(default_fps))


def _lock_connections():
    if nuke.numvalue('preferences.wlf_lock_connections', 0.0):
        nuke.Root()['lock_connections'].setValue(1)
        nuke.Root().setModified(False)


def _jump_frame():
    if nuke.numvalue('preferences.wlf_jump_frame', 0.0):
        n = wlf_write_node()
        if n:
            nuke.frame(n['frame'].value())
            nuke.Root().setModified(False)


@abort_modified
def _send_to_render_dir():
    if nuke.numvalue('preferences.wlf_send_to_dir', 0.0):
        asset.sent_to_dir(
            unicode(nuke.value('preferences.wlf_render_dir'), 'UTF-8'))


@abort_modified
def _render_jpg():
    if nuke.numvalue('preferences.wlf_render_jpg', 0.0):
        n = wlf_write_node()
        if n:
            print('render_jpg: {}'.format(n.name()))
            try:
                n['bt_render_JPG'].execute()
            except RuntimeError as ex:
                nuke.message(str(ex))


def _gizmo_to_group_on_create():
    n = nuke.thisNode()
    if not nuke.numvalue('preferences.wlf_gizmo_to_group', 0.0):
        return

    if not isinstance(n, nuke.Gizmo):
        return

    # Avoid scripted gizmo.
    if nuke.knobChangeds.get(n.Class()):
        return

    n.addKnob(nuke.Text_Knob('wlf_gizmo_to_group'))


def _gizmo_to_group_update_ui():
    n = nuke.thisNode()
    _temp_knob_name = 'wlf_gizmo_to_group'
    _has_temp_knob = nuke.exists('{}.{}'.format(n.name(), _temp_knob_name))

    if _has_temp_knob:
        n = edit.gizmo_to_group(n)
        n.removeKnob(n[_temp_knob_name])
        n.removeKnob(n['User'])


def _autoplace():
    if nuke.numvalue('preferences.wlf_autoplace', 0.0) and nuke.Root().modified():
        if nuke.numvalue('preferences.wlf_autoplace_type', 0.0) == 0.0:
            orgnize.autoplace()
        else:
            map(nuke.autoplace, nuke.allNodes())


def _print_name():
    print(nuke.thisNode().name())


def _add_root_info():
    """add info to root.  """

    artist = nuke.value('preferences.wlf_artist', '')
    if not artist:
        return
    if not nuke.exists('root.wlf'):
        n = nuke.Root()
        k = nuke.Tab_Knob('wlf', '吾立方')
        k.setFlag(nuke.STARTLINE)
        n.addKnob(k)

        k = nuke.String_Knob('wlf_artist', '制作人')
        k.setFlag(nuke.STARTLINE)
        k.setValue(artist)
        n.addKnob(k)
    else:
        if nuke.exists('root.wlf_artist') and not nuke.value('root.wlf_artist', ''):
            nuke.knob('root.wlf_artist', artist)


def create_out_dirs():
    """Create this read node's output dir if need."""

    target_dir = os.path.dirname(nuke.filename(nuke.thisNode()))
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
