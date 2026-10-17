Development sources
===================

.. currentmodule:: packaging.dependency_sources

Parse the contents of a ``[develop.sources]`` table and choose a source for one
dependency occurrence. The caller supplies the relevant extra or dependency
group and any target environment overrides.

.. doctest::

    >>> from packaging.dependency_sources import parse_dependency_sources, select_dependency_source
    >>> sources = parse_dependency_sources({"core": [
    ...     {"path": "../core-fast", "extra": "fast"},
    ...     {"path": "../core"},
    ... ]})
    >>> select_dependency_source(sources["core"], extra="fast").location
    '../core-fast'
    >>> select_dependency_source(sources["core"]).location
    '../core'

The parser normalizes package and selector names and rejects invalid entries.
Selection returns the first matching entry, or ``None`` when none applies.
Marker environments follow :meth:`packaging.markers.Marker.evaluate`.

Paths remain relative and credential placeholders remain literal. Callers choose
which files to read, merge their tables, resolve paths against the declaring
file, and apply installation policies. Hash syntax is checked here; callers
choose supported algorithms and verify the downloaded bytes. Source tables do
not add dependencies or change a requirement's version constraints.

Reference
---------

.. autoclass:: DependencySource
    :members:

.. autofunction:: parse_dependency_sources

.. autofunction:: select_dependency_source

.. autoexception:: InvalidDependencySource
