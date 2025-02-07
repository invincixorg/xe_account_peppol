from odoo import fields, models, api, _, tools, Command
from odoo.exceptions import AccessError, ValidationError

import requests
import json
import logging

_logger = logging.getLogger(__name__)


class Company(models.Model):
    _inherit = "res.company"

    is_enable_peppol = fields.Boolean('Enable PEPPOL E-Invoicing')
    client_id = fields.Char(string="Client ID")
    client_number = fields.Char(string="Client Number")
    peppol_endpoint = fields.Char(string="PEPPOL ID",
                                  help="Unique identifier used by the BIS Billing 3.0 and its derivatives, also known as 'Endpoint ID'.")
    account_peppol_verification_status = fields.Selection(
        selection=[('not_verified', 'Not verified yet'), ('verified', 'Verified'),],
        string='PEPPOL Endpoint Verification',
        copy=False,
        default='not_verified'
    )
    account_peppol_edi_api_key = fields.Char(string='API Key', help="Add an API key authenticate with the middleware to send E-Invoices")
    account_peppol_edi_mode = fields.Selection(selection=[('test', 'Test'), ('prod', 'Live')], default='test')
    account_peppol_edi_access_token = fields.Char(string='PEPPOL Access Token')
    account_peppol_edi_refresh_token = fields.Char(string='PEPPOL Refresh Token')

    def get_is_peppol_enabled(self):
        company = self.env.company
        return company.is_enable_peppol and company.account_peppol_verification_status == 'verified'

    def _get_server_url(self):
        if self.account_peppol_edi_mode == 'prod':
            url = self.env['ir.config_parameter'].sudo().get_param('xe_account_peppol.edi.prod.url')
            message = "Sorry, PEPPOL Production URL is not set in the system. Please contact your administrator."
        else:
            url = self.env['ir.config_parameter'].sudo().get_param('xe_account_peppol.edi.test.url')
            message = "Sorry, PEPPOL Test URL is not set in the system. Please contact your administrator."
        if not url:
            raise AccessError(message)
        return url

    def _make_request(self, url, payload=None, headers=None, method=None):
        account_peppol_edi_access_token = self.account_peppol_edi_access_token
        headers['Authorization'] = f'Bearer {account_peppol_edi_access_token}'
        try:
            response = requests.request(method, url, headers=headers, data=json.dumps(payload))
            print(json.loads(response.text))
            _logger.info(f'response --------- {url, headers, payload, response}')
        except Exception as e:
            raise AccessError(e)
        if response.status_code == 401:
            self.env['res.config.settings'].action_regenerate_tokens(self)
            print('Token Regenerate----------------------------------------')
            response = self._make_request(url, payload, headers, method)
            print('Recursive Request Call----------------------------------')
        return response
