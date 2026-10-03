"""Tests for bikephysics.core.mathx (architecture.md S5.2, task.md T1)."""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.core.mathx import casadi_backend, get_backend, numpy_backend


def test_numpy_backend_basic_ops() -> None:
    assert numpy_backend.sin(0.0) == 0.0
    assert numpy_backend.cos(0.0) == 1.0
    np.testing.assert_allclose(numpy_backend.sqrt(np.array([4.0, 9.0])), [2.0, 3.0])
    assert numpy_backend.maximum(1.0, 2.0) == 2.0
    assert numpy_backend.minimum(1.0, 2.0) == 1.0


def test_sign_smooth_is_bounded_and_odd() -> None:
    eps = 0.1
    assert numpy_backend.sign_smooth(0.0, eps) == 0.0
    large_positive = numpy_backend.sign_smooth(10.0, eps)
    large_negative = numpy_backend.sign_smooth(-10.0, eps)
    assert large_positive > 0.999
    assert large_negative < -0.999
    assert np.isclose(large_positive, -large_negative)


def test_sign_smooth_sharpens_as_eps_shrinks() -> None:
    v = 0.05
    loose = numpy_backend.sign_smooth(v, eps=1.0)
    tight = numpy_backend.sign_smooth(v, eps=0.001)
    assert tight > loose  # smaller eps -> closer to the hard sign()


def test_casadi_backend_stub_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="T9"):
        casadi_backend.sin(0.0)
    with pytest.raises(NotImplementedError, match="T9"):
        casadi_backend.sign_smooth(0.0, 0.1)


def test_get_backend_selects_by_name() -> None:
    assert get_backend("numpy") is numpy_backend
    assert get_backend("casadi") is casadi_backend
    assert get_backend() is numpy_backend


def test_get_backend_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown math backend"):
        get_backend("tensorflow")
