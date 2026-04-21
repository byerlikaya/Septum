"""Enable ``python -m septum_gateway`` to launch the worker."""

from .worker import main

raise SystemExit(main())
