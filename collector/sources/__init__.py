"""Source registry.

A source is a function ``collect(ctx) -> dict`` registered under a ``type`` name
used in config/widgets.yaml. ``view`` names the front-end renderer
(site/js/renderers/<view>.js) that knows how to draw its output.

To add a new kind of widget: create a module here, decorate its collect
function with @source("my_type", view="list" or a new view), import it below,
and (if the view is new) add a renderer in site/js/renderers/.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Source:
    type: str
    view: str
    collect: Callable


REGISTRY: dict[str, Source] = {}


def source(type_name: str, view: str):
    def deco(fn):
        if type_name in REGISTRY:
            raise RuntimeError(f"duplicate source type {type_name}")
        REGISTRY[type_name] = Source(type_name, view, fn)
        return fn
    return deco


# Import modules so they register themselves.
from . import rss, arxiv, crossref, stock, aaa_gas, costco_gas, macro, treasury, espn, candles  # noqa: E402,F401
