# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import operator
import string
from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.markers import Marker
from packaging.requirements import Requirement

from .strategies import SETTINGS

pytestmark = pytest.mark.property

ENVIRONMENT = {"os_name": "posix", "sys_platform": "linux"}
WHITESPACE = st.sampled_from(["", " ", "\t", " \t "])
STRING_CHARACTERS = (
    string.ascii_letters + string.digits + " \t" + string.punctuation.replace("\\", "")
)
RELEASES = st.tuples(*(st.integers(min_value=0, max_value=20) for _ in range(3)))
COMPARISONS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class Expression(NamedTuple):
    """A marker expression and its independently computed truth value."""

    source: str
    expected: bool


@st.composite
def string_comparisons(draw: st.DrawFn) -> Expression:
    """Generate comparisons of String fields, including case-sensitive matches."""
    variable = draw(st.sampled_from(list(ENVIRONMENT)))
    quote = draw(st.sampled_from(["'", '"']))
    field_value = ENVIRONMENT[variable]
    values = st.text(alphabet=STRING_CHARACTERS.replace(quote, ""), max_size=12)
    value = draw(
        st.one_of(
            values,
            st.sampled_from(
                [
                    field_value,
                    field_value.upper(),
                    " " + field_value,
                    field_value + "\t",
                    "",
                ]
            ),
        )
    )
    operation = draw(st.sampled_from(["==", "!=", "in", "not in"]))
    literal = f"{quote}{value}{quote}"
    lhs, rhs = field_value, value
    left, right = variable, literal
    if draw(st.booleans()):
        lhs, rhs = rhs, lhs
        left, right = right, left

    if operation == "in":
        expected = lhs in rhs
    elif operation == "not in":
        expected = lhs not in rhs
    elif operation == "==":
        expected = lhs == rhs
    else:
        expected = lhs != rhs

    return Expression(f"{left} {operation} {right}", expected)


def combine(left: Expression, operation: str, right: Expression) -> Expression:
    """Compose a parenthesized expression and its Boolean result."""
    if operation == "and":
        expected = left.expected and right.expected
    else:
        expected = left.expected or right.expected

    return Expression(f"({left.source}) {operation} ({right.source})", expected)


EXPRESSIONS = st.recursive(
    string_comparisons(),
    lambda children: st.builds(
        combine, children, st.sampled_from(["and", "or"]), children
    ),
    max_leaves=10,
)


class TestStringFieldComparisons:
    """String comparisons are
    "case sensitive, with no value normalization of any kind".

    https://packaging.python.org/en/latest/specifications/dependency-specifiers/#marker-comparisons
    """

    @given(comparison=string_comparisons(), before=WHITESPACE, after=WHITESPACE)
    @SETTINGS
    def test_comparisons_follow_python_string_operations(
        self, comparison: Expression, before: str, after: str
    ) -> None:
        marker = Marker(before + comparison.source + after)
        assert marker.evaluate(ENVIRONMENT) == comparison.expected


class TestBooleanMarkerGrammar:
    """The dependency marker grammar specifies::

        marker_and    = marker_expr wsp* 'and' marker_expr
                      | marker_expr
        marker_or     = marker_and wsp* 'or' marker_and
                      | marker_and

    https://packaging.python.org/en/latest/specifications/dependency-specifiers/#grammar
    """

    @given(expression=EXPRESSIONS)
    @SETTINGS
    def test_parentheses_preserve_the_boolean_tree(
        self, expression: Expression
    ) -> None:
        assert Marker(expression.source).evaluate(ENVIRONMENT) == expression.expected

    @given(a=string_comparisons(), b=string_comparisons(), c=string_comparisons())
    @SETTINGS
    def test_and_binds_more_tightly_than_or(
        self, a: Expression, b: Expression, c: Expression
    ) -> None:
        source = f"{a.source} or {b.source} and {c.source}"
        assert Marker(source).evaluate(ENVIRONMENT) == (
            a.expected or (b.expected and c.expected)
        )
        grouped = f"({a.source} or {b.source}) and {c.source}"
        assert Marker(grouped).evaluate(ENVIRONMENT) == (
            (a.expected or b.expected) and c.expected
        )


class TestRequirementReleaseOrdering:
    """Comparison and ordering of release segments considers the numeric value of
    each component of the release segment in turn.

    https://packaging.python.org/en/latest/specifications/version-specifiers/#final-releases
    """

    @given(
        required=RELEASES,
        candidate=RELEASES,
        operation=st.sampled_from(list(COMPARISONS)),
        whitespace=WHITESPACE,
    )
    @SETTINGS
    def test_requirement_version_limits_follow_release_order(
        self,
        required: tuple[int, int, int],
        candidate: tuple[int, int, int],
        operation: str,
        whitespace: str,
    ) -> None:
        version = ".".join(map(str, required))
        requirement = Requirement(f"demo{whitespace}{operation}{whitespace}{version}")
        candidate_text = ".".join(map(str, candidate))
        assert requirement.specifier.contains(candidate_text) == COMPARISONS[operation](
            candidate, required
        )
