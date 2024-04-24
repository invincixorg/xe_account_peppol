# -*- coding: utf-8 -*-
{
    "name": "EDI: PEPPOL",
    'version': '15.0.0.1',
    'author': 'INVINCIX Solutions Pte Ltd',
    'maintainer': 'INVINCIX Solutions Pte Ltd',
    'company': 'INVINCIX Solutions Pte Ltd',
    'website': "https://www.invincix.com",
    "category": "Accounting",
    "summary": "E-Invoicing integration with PEPPOL Network",
    "description": """
    - Seamless integration with the PEPPOL network
    - Register as a PEPPOL participant
    - Send and receive e-invoices electronically
    - Create, manage, and track invoices with ease
    """,
    'license': 'LGPL-3',
    "depends": ["account"],
    "data": [
        'security/security.xml',
        'views/account_move_views.xml',
        'views/res_company_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings.xml',
        'security/ir.model.access.csv',
    ],
    'assets': {
        'web.assets_backend': [
            'xe_account_peppol/static/src/js/fetch_peppol_edi_button.js',
            'xe_account_peppol/static/src/js/users_menu.js',
        ],
        'web.assets_qweb': [
            'xe_account_peppol/static/src/xml/fetch_peppol_edi_button.xml',
        ],
    },
    "images": ["static/description/img/banner.png"],
    "installable": True,
    "application": True,
    "auto_install": False,
}
