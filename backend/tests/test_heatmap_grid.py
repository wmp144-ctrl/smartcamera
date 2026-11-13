"""Heatmap resampling (the UI may ask for any grid resolution)."""

from __future__ import annotations

from app.services.heatmap import clamp_grid, rebin


def test_rebin_keeps_the_total_on_a_coarser_grid() -> None:
    cells = [0] * (8 * 6)
    cells[0] = 40
    cells[8 * 6 - 1] = 10

    out = rebin(cells, 8, 6, 4, 3)

    assert len(out) == 4 * 3
    assert sum(out) == 50
    assert out[0] == 40
    assert out[-1] == 10


def test_rebin_splits_a_coarse_cell_over_a_finer_grid() -> None:
    cells = [0, 0, 0, 0]  # 2x2, only the top-left cell is hot
    cells[0] = 40

    out = rebin(cells, 2, 2, 4, 4)

    assert len(out) == 16
    assert sum(out) == 40
    # The source cell is half the image; it splits evenly over the four target
    # cells it covers and nothing leaks into the rest of the grid.
    assert [value for value in out if value] == [10, 10, 10, 10]
    assert out[2] == 0 and out[8] == 0


def test_rebin_is_a_no_op_for_the_same_grid() -> None:
    cells = [1, 2, 3, 4]
    assert rebin(cells, 2, 2, 2, 2) == cells


def test_clamp_grid_bounds_the_requested_resolution() -> None:
    assert clamp_grid(None, None) == (64, 36)
    assert clamp_grid(2, 1000) == (4, 120)
    assert clamp_grid(500, 2) == (160, 4)
