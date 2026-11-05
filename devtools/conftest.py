"""Pytest config for the devtools package.

Template files under ``devtools/templates`` belong to the *generated* projects, not to
this repository, so they must never be collected as tests here (they also use a ``.tmpl``
suffix, this is just a second safety net).
"""

collect_ignore_glob = ["templates/*", "templates/**/*"]
