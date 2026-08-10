"""Presentation of crawl results, kept separate from extraction and storage."""

from .collect import DashboardData, collect_dashboard_data
from .dashboard import render_dashboard, write_dashboard

__all__ = [
    "DashboardData",
    "collect_dashboard_data",
    "render_dashboard",
    "write_dashboard",
]
