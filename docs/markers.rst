Markers
=======

.. currentmodule:: packaging.markers

One extra requirement of dealing with dependencies is the ability to specify
if it is required depending on the operating system or Python version in use.
The :ref:`specification of dependency specifiers <pypug:dependency-specifiers>`
defines the scheme which has been implemented by this module.

Usage
-----

.. doctest::

    >>> from packaging.markers import Marker, UndefinedEnvironmentName
    >>> marker = Marker("python_version>'2'")
    >>> marker
    <Marker('python_version > "2"')>
    >>> # We can evaluate the marker to see if it is satisfied
    >>> marker.evaluate()
    True
    >>> # We can also override the environment
    >>> env = {'python_version': '1.5'}
    >>> marker.evaluate(environment=env)
    False
    >>> # Multiple markers can be ANDed
    >>> and_marker = Marker("os_name=='a' and os_name=='b'")
    >>> and_marker
    <Marker('os_name == "a" and os_name == "b"')>
    >>> # Multiple markers can be ORed
    >>> or_marker = Marker("os_name=='a' or os_name=='b'")
    >>> or_marker
    <Marker('os_name == "a" or os_name == "b"')>
    >>> # Markers can be also used with extras, to pull in dependencies if
    >>> # a certain extra is being installed
    >>> extra = Marker('extra == "bar"')
    >>> # You can do simple comparisons between marker objects:
    >>> Marker("python_version > '3.6'") == Marker("python_version > '3.6'")
    True
    >>> # You can also perform simple comparisons between sets of markers:
    >>> markers1 = {Marker("python_version > '3.6'"), Marker('os_name == "unix"')}
    >>> markers2 = {Marker('os_name == "unix"'), Marker("python_version > '3.6'")}
    >>> markers1 == markers2
    True

Combining markers programmatically
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:class:`Marker` objects support the ``&`` (and) and ``|`` (or) operators for
combining markers without manually constructing marker strings:

.. doctest::

    >>> from packaging.markers import Marker
    >>> py3 = Marker("python_version >= '3'")
    >>> linux = Marker("sys_platform == 'linux'")
    >>> combined = py3 & linux
    >>> str(combined)
    'python_version >= "3" and sys_platform == "linux"'
    >>> either = py3 | linux
    >>> str(either)
    'python_version >= "3" or sys_platform == "linux"'

This is equivalent to writing the combined marker string directly but is useful
when building markers dynamically from separate conditions.

.. versionadded:: 26.1

Evaluating many markers against one environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`Marker.evaluate` builds the environment it evaluates against on every
call: it copies the detected environment, adds the keys the context defines,
applies the caller's overrides, canonicalizes ``extra`` and repairs the
``python_full_version`` a non-tagged build reports. Evaluating a whole
dependency graph against one environment repeats that per marker.
:func:`prepare_environment` does it once and returns a plain dict, which
:meth:`Marker.evaluate_prepared` reads as given:

.. doctest::

    >>> from packaging.markers import Marker, prepare_environment
    >>> environment = prepare_environment({"os_name": "posix"})
    >>> markers = [Marker("os_name == 'posix'"), Marker("os_name == 'nt'")]
    >>> [marker.evaluate_prepared(environment) for marker in markers]
    [True, False]

``marker.evaluate(environment, context)`` and
``marker.evaluate_prepared(prepare_environment(environment, context))`` return
the same answer for every input: :meth:`Marker.evaluate` runs those two steps.

:meth:`Marker.evaluate_prepared` accepts any mapping and normalizes nothing. A
caller may keep one prepared environment and write a key into it between
evaluations, keeping the kind of value that key carries: a string for ``extra``,
a set for ``extras`` and ``dependency_groups``. Pass an ``extra`` through
:func:`packaging.utils.canonicalize_name` first, since a non-canonical name will
not match the marker's own literal, which was canonicalized when it was parsed.

A key the marker names and the mapping does not carry raises
:class:`UndefinedEnvironmentName`, but a mapping :func:`prepare_environment` did
not build can answer rather than raise: on a non-tagged build
:func:`default_environment` reports a ``python_full_version`` ending in ``+``,
so both ``>=`` and ``<`` against it are ``False``.

.. versionadded:: 26.4


Reference
---------

.. automodule:: packaging.markers
    :members:
    :special-members: __and__, __or__
    :exclude-members: __init__
