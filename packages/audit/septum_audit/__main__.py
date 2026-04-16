"""Enable ``python -m septum_audit`` to launch the worker."""

from .worker import main

raise SystemExit(main())
