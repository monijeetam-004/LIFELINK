# Phase F: importing any service registers the real-time outbox listeners
# (SQLAlchemy Session events) for every process that touches the domain --
# the API, RQ workers, and scripts alike -- so no mutation path can skip
# event creation just because it didn't happen to import the module.
from app.services import realtime_service as _realtime_service  # noqa: F401
