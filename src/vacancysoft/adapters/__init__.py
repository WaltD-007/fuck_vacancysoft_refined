from vacancysoft.adapters.adzuna import AdzunaAdapter
from vacancysoft.adapters.ashby import AshbyAdapter
from vacancysoft.adapters.eightfold import EightfoldAdapter
from vacancysoft.adapters.generic_browser import GenericBrowserAdapter
from vacancysoft.adapters.google_jobs import GoogleJobsAdapter
from vacancysoft.adapters.greenhouse import GreenhouseAdapter
from vacancysoft.adapters.icims import IcimsAdapter
from vacancysoft.adapters.lever import LeverAdapter
from vacancysoft.adapters.smartrecruiters import SmartRecruitersAdapter
from vacancysoft.adapters.workable import WorkableAdapter
from vacancysoft.adapters.workday import WorkdayAdapter, derive_workday_candidate_endpoints

__all__ = [
    "AdzunaAdapter",
    "AshbyAdapter",
    "EightfoldAdapter",
    "GenericBrowserAdapter",
    "GoogleJobsAdapter",
    "GreenhouseAdapter",
    "IcimsAdapter",
    "LeverAdapter",
    "SmartRecruitersAdapter",
    "WorkableAdapter",
    "WorkdayAdapter",
    "derive_workday_candidate_endpoints",
]
