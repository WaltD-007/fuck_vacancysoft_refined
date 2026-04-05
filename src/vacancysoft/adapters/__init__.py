from vacancysoft.adapters.adzuna import AdzunaAdapter
from vacancysoft.adapters.eightfold import EightfoldAdapter
from vacancysoft.adapters.generic_browser import GenericBrowserAdapter
from vacancysoft.adapters.google_jobs import GoogleJobsAdapter
from vacancysoft.adapters.greenhouse import GreenhouseAdapter
from vacancysoft.adapters.workable import WorkableAdapter
from vacancysoft.adapters.workday import WorkdayAdapter, derive_workday_candidate_endpoints

__all__ = [
    "AdzunaAdapter",
    "EightfoldAdapter",
    "GenericBrowserAdapter",
    "GoogleJobsAdapter",
    "GreenhouseAdapter",
    "WorkableAdapter",
    "WorkdayAdapter",
    "derive_workday_candidate_endpoints",
]
