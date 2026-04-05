from vacancysoft.adapters.adzuna import AdzunaAdapter
from vacancysoft.adapters.ashby import AshbyAdapter
from vacancysoft.adapters.efinancialcareers import EFinancialCareersAdapter
from vacancysoft.adapters.eightfold import EightfoldAdapter
from vacancysoft.adapters.generic_browser import GenericBrowserAdapter
from vacancysoft.adapters.google_jobs import GoogleJobsAdapter
from vacancysoft.adapters.greenhouse import GreenhouseAdapter
from vacancysoft.adapters.icims import IcimsAdapter
from vacancysoft.adapters.lever import LeverAdapter
from vacancysoft.adapters.oracle_cloud import OracleCloudAdapter
from vacancysoft.adapters.reed import ReedAdapter
from vacancysoft.adapters.smartrecruiters import SmartRecruitersAdapter
from vacancysoft.adapters.successfactors import SuccessFactorsAdapter
from vacancysoft.adapters.workable import WorkableAdapter
from vacancysoft.adapters.workday import WorkdayAdapter, derive_workday_candidate_endpoints

__all__ = [
    "AdzunaAdapter",
    "AshbyAdapter",
    "EFinancialCareersAdapter",
    "EightfoldAdapter",
    "GenericBrowserAdapter",
    "GoogleJobsAdapter",
    "GreenhouseAdapter",
    "IcimsAdapter",
    "LeverAdapter",
    "OracleCloudAdapter",
    "ReedAdapter",
    "SmartRecruitersAdapter",
    "SuccessFactorsAdapter",
    "WorkableAdapter",
    "WorkdayAdapter",
    "derive_workday_candidate_endpoints",
]
