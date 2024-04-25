from odoo import fields, models, api, _
from odoo.exceptions import AccessError, ValidationError

import json

HEADERS = {
    'Content-Type': 'application/json',
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    is_enable_peppol = fields.Boolean(string='Enable PEPPOL E-Invoicing', related='company_id.is_enable_peppol', readonly=False)
    client_id = fields.Char(string="Client ID", related='company_id.client_id', readonly=False)
    client_number = fields.Char(string="Client Number", related='company_id.client_number', readonly=False)
    peppol_endpoint = fields.Char(string="PEPPOL ID", related='company_id.peppol_endpoint', readonly=False,
                                  help="Unique identifier used by the BIS Billing 3.0 and its derivatives, also known as 'Endpoint ID'.")
    account_peppol_verification_status = fields.Selection(
        selection=[('not_verified', 'Not verified yet'), ('verified', 'Verified')],
        string='PEPPOL Endpoint Verification',
        copy=False,
        related='company_id.account_peppol_verification_status',
    )
    account_peppol_edi_api_key = fields.Char(string='API Key', related='company_id.account_peppol_edi_api_key', readonly=False, help="Add an API key authenticate with the middleware to send E-Invoices")
    account_peppol_edi_mode = fields.Selection(
        selection=[('test', 'Test'), ('prod', 'Live')], related='company_id.account_peppol_edi_mode', readonly=False,
    )
    account_peppol_edi_url = fields.Char(string='PEPPOL URL', related='company_id.account_peppol_edi_url', readonly=False)
    account_peppol_edi_access_token = fields.Char(string='PEPPOL Access Token', related='company_id.account_peppol_edi_access_token', readonly=False)
    account_peppol_edi_refresh_token = fields.Char(string='PEPPOL Refresh Token', related='company_id.account_peppol_edi_refresh_token', readonly=False)

    def _get_server_url(self):
        urls = {
            'prod': 'https://api.invoicedge.app',
            'test': 'https://cc2c-59-153-17-41.ngrok-free.app',
        }
        return urls

    @api.onchange('account_peppol_edi_mode')
    def _onchange_account_peppol_edi_mode(self):
        if self.account_peppol_edi_mode:
            urls = self._get_server_url()
            self.account_peppol_edi_url = urls[self.account_peppol_edi_mode]

    def action_validate_peppol(self):
        '''
        This method is to validate the company based on the API Key provided by the service provider.
        :return: Updates the access token & refresh token with the values received from the api response. Also updates the verification status.
        :raise: AccessError: If API Key is invalid or any other exception occurs
        :raise: ValidationError: If Company UEN No. & Company Email does not match the response UEN No. & Company Email
        '''
        if not self.account_peppol_edi_api_key:
            raise ValidationError('Sorry! You have not inputted any API Key. Please contact your service provider for the API Key to access PEPPOL Network.')
        if not self.account_peppol_edi_mode:
            raise ValidationError('Sorry! You have not chosen PEPPOL Electronic Document Mode (Test/Live).')
        url = self.account_peppol_edi_url
        try:
            response = self.company_id._make_request(
                f"{url}/api/v1/auth/verify-api-key",
                payload={'api_key': self.company_id.account_peppol_edi_api_key}, headers=HEADERS, method="POST"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                if json_response.get('message') == 'INVALID_API_KEY':
                    raise AccessError('Sorry! you have inputted an invalid API Key. Please contact your service provider for the API Key to access PEPPOL Network.')
                if json_response.get('message'):
                    raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            if self.company_id.l10n_sg_unique_entity_number != json_response.get('uen_no'):
                raise ValidationError('Sorry, Company UEN No. does not match. Use proper Company UEN No. to validate.')
            if self.company_id.email != json_response.get('email'):
                raise ValidationError('Sorry, Company Email does not match. Use proper Company Email to validate.')
            self.company_id.write({
                'client_id': json_response.get('client_id'),
                'client_number': json_response.get('client_number'),
                'peppol_endpoint': json_response.get('peppol_id'),
                'account_peppol_edi_access_token': json_response.get('accessToken'),
                'account_peppol_edi_refresh_token': json_response.get('refreshToken'),
                'account_peppol_verification_status': 'verified'
            })

    def action_regenerate_tokens(self, company_id=False):
        '''
        This method is to regenerate the access token based on the refresh token.
        If refresh token is expired then it calls the action_validate_peppol() method to verify the company
        to get the new access token & refresh token with the API Key.
        :param company_id: Currently active company
        :return: Updates the access token & refresh token with the values received from the api response
        :raise: AccessError: If any exception occurs
        '''
        company_id = company_id or self.env.company
        url = company_id.account_peppol_edi_url
        account_peppol_edi_refresh_token = company_id.account_peppol_edi_refresh_token
        try:
            HEADERS['Authorization'] = f'Bearer {account_peppol_edi_refresh_token}'
            response = company_id._make_request(
                f"{url}/api/v1/auth/refresh",
                payload={}, headers=HEADERS, method="POST"
            )
            json_response = json.loads(response.text)
            if response.status_code == 400:
                return self.action_validate_peppol()
            if not (200 <= response.status_code <= 299):
                if json_response.get('message'):
                    raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            company_id.write({
                'account_peppol_edi_access_token': json_response.get('accessToken'),
                'account_peppol_edi_refresh_token': json_response.get('refreshToken'),
            })
