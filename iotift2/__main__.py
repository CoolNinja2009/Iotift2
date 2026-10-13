if __package__:
    from .cli import main
else:
    # Run as `py iotift2 ...` (the folder as a script): make the package importable.
    import os
    import sys
    sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from iotift2.cli import main

main()
