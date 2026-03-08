"""
WSGI config for myproject project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

application = get_wsgi_application()

# In preload mode, Master initializes settings before fork so Workers
# share the read-only memory via Copy-on-Write. This avoids redundant
# Settings initialization in each Worker.
try:
    from selfhealing.settings import get_config

    get_config()
except ImportError:
    pass
