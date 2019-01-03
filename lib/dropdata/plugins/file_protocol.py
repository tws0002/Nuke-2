# -*- coding=UTF-8 -*-
"""File protocol dropdata handle.  """

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import re
import urllib

from ..core import HOOKIMPL

# pylint: disable=missing-docstring


@HOOKIMPL
def get_url(data):
    match = re.match(r'file://+([^/].*)', data)
    if match:
        _data = match.group(1)
        _data = urllib.unquote(_data)
        return [_data]
    return None
