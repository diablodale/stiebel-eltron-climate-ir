"""One subpackage per supported appliance.

Nothing is re-exported here. The host test suite and `tools/` import a model's
protocol module as a bare `devices.<model>.protocol`, outside Home Assistant
entirely, which only works while the package files on that path stay free of
`homeassistant` imports. Pulling an entity class up into this module would break
that for every model at once.
"""
