# Tests package for Open Wearables backend

# Before any module here imports `app`: `app.config` builds Settings() at import,
# and would read a real config/.env if one were present (tests/_real_env_guard.py).
from tests._real_env_guard import refuse_real_env_file, refuse_to_run_after_app

refuse_to_run_after_app()
refuse_real_env_file()
