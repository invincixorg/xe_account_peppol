from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, AccessError

import requests
import json
import logging

_logger = logging.getLogger(__name__)

HEADERS = {
    'Content-Type': 'application/json',
}


class Partner(models.Model):
    _inherit = "res.partner"

    debtor_id = fields.Integer(string="Debtor ID", store=True, readonly=True, tracking=True)
    debtor_number = fields.Char(string="Debtor No.", store=True, readonly=True, tracking=True)
    creditor_id = fields.Integer(string="Creditor ID", store=True, tracking=True)
    creditor_number = fields.Char(string="Creditor Number.", store=True, readonly=True, tracking=True)
    client_id = fields.Integer(string="Client ID", store=True, readonly=True, tracking=True)
    peppol_endpoint = fields.Char(
        string="PEPPOL ID",
        help="Unique identifier used by the BIS Billing 3.0 and its derivatives, also known as 'Endpoint ID'.",
        store=True, readonly=True, tracking=True)

    def _get_account_peppol_edi_url(self):
        url = self.env.company.account_peppol_edi_url
        if not url:
            raise AccessError("Sorry, PEPPOL URL is not set in the system. Please contact your administrator.")
        return url

    @api.model
    def _name_search(self, name='', args=None, operator='ilike', limit=100, name_get_uid=None):
        args = list(args or [])
        if name:
            args += ['|', ('name', operator, name), ('l10n_sg_unique_entity_number', operator, name)]
        return self._search(args, limit=limit, access_rights_uid=name_get_uid)

    @api.constrains("l10n_sg_unique_entity_number")
    def _check_unique_l10n_sg_unique_entity_number(self):
        for rec in self:
            if rec.l10n_sg_unique_entity_number:
                msg = "UEN No. '%s' already exists in the system!" % rec.l10n_sg_unique_entity_number
                envobj = self.env['res.partner'].search(
                    [('id', '!=', rec.id), ('l10n_sg_unique_entity_number', '=', rec.l10n_sg_unique_entity_number), '|',
                     ('active', '=', True), ('active', '=', False)], limit=1)
                if envobj:
                    raise ValidationError(msg)

    def action_fetch_peppol_endpoint(self):
        '''
        This method is to fetch the PEPPOL Endpoint of the customer/vendor in the Odoo from PEPPOL.
        :return: Updates the Debtor ID, Client ID, Debtor Number, UEN No.
        :raise: AccessError: If any exception occurs
        '''
        if not self.country_id:
            raise ValidationError('Sorry, you have not chosen the country.')
        if not self.l10n_sg_unique_entity_number:
            raise ValidationError('Sorry, you have not entered the UEN Number.')

        lang = "EN"
        if self.lang:
            lang = self.lang.split("_")[0]
        # Call Middleware API to create Debtor/Customer in Peppol
        url = self.env.company.account_peppol_edi_url
        payload = {
            "platform_id": 15,
            "client_number": int(self.env.user.company_id.client_number),
            "name": self.name,
            "country_code": self.country_id.code,
            "language_code": lang,
            "debtor_type": "residential",
            "preferred_channel": "openpeppol",
            "address": self.street or self.street2 or '',
            "zip_code": self.zip or '',
            "city": self.city or '',
            "email": self.email or '',
            "phone_number": self.phone or '',
            "debtor_reference": "Test",
            "legal_entity_trn": self.l10n_sg_unique_entity_number
        }
        try:
            client_number = self.env.user.company_id.client_number
            HEADERS['x-client-number'] = client_number
            response = self._make_request(
                f"{url}/api/v1/debtors",
                payload=payload, headers=HEADERS, method="POST"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                message = json.loads(response.text).get('message')
                if message == 'Invalid legal_entity_trn':
                    raise AccessError('Sorry, you have entered an invalid UEN Number.')
                if message == 'legal_entity_trn is duplicated':
                    raise AccessError('Sorry, you have entered an UEN Number that already exists in Peppol.')
                raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            self.debtor_id = json_response['id']
            self.debtor_number = json_response['debtor_number']
            self.client_id = json_response['client_id']
            self.peppol_endpoint = json_response['peppol_id']
            self.l10n_sg_unique_entity_number = json_response['legal_entity_trn']

        log_message = _('The debtor has been created on PEPPOL.')
        self._message_log(body=log_message)

    def _make_request(self, url, payload=None, headers=None, method=None):
        account_peppol_edi_access_token = self.company_id.account_peppol_edi_access_token or self.env.user.company_id.account_peppol_edi_access_token
        headers['Authorization'] = f'Bearer {account_peppol_edi_access_token}'
        try:
            response = requests.request(method, url, headers=headers, data=json.dumps(payload))
            print(json.loads(response.text))
            _logger.info(f'response --------- {url, headers, payload, response}')
        except Exception as e:
            raise AccessError(e)
        if response.status_code == 401:
            self.env['res.config.settings'].action_regenerate_tokens(self.env.user.company_id)
            print('Token Regenerate----------------------------------------')
            response = self._make_request(url, payload, headers, method)
            print('Recursive Request Call----------------------------------')
        return response
