import importlib
import pkgutil
import learnerbot

def test_all_learnerbot_modules_import():
    failures=[]
    for m in pkgutil.iter_modules(learnerbot.__path__):
        if m.name == '__main__':
            continue
        name='learnerbot.'+m.name
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append((name,type(exc).__name__,str(exc)))
    assert not failures, failures
