from importlib import import_module

def invoke_backend_action(backend: str, action: str, **kwargs):
    """
    Dynamically import backends.<backend>.automation and run action_<action>.
    """
    mod = import_module(f"backends.{backend}.automation")
    fn_name = f"action_{action.replace('-', '_')}"
    if not hasattr(mod, fn_name):
        raise ValueError(f"No such action '{fn_name}' in {mod}")
    fn = getattr(mod, fn_name)
    return fn(**kwargs)