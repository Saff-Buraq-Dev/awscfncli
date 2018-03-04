# -*- encoding: utf-8 -*-

__author__ = 'kotaimen'
__date__ = '28-Feb-2018'

"""Main cli entry point, called when awscfncli is run as a package,  
imported in setuptools intergration.

cli package stucture:

    Click main entry:
        cli/main.py
      
    Command groups:
        cli/group_named/__init__.py
    
    Subcommands:   
        cli/group_name/command_name.py

All commands are imported in cli/__init__.py to get registered into click. 
"""

from .cli import cfn_cli


def main():
    cfn_cli()


if __name__ == '__main__':
    main()
