from odoo import fields, models, api, _, tools, Command
from odoo.exceptions import AccessError, ValidationError

import requests
import json
import logging

_logger = logging.getLogger(__name__)

HEADERS = {
    'Content-Type': 'application/json',
}


class AccountMove(models.Model):
    _inherit = 'account.move'

    # peppol_status = fields.Char(string="PEPPOL Status", tracking=True)
    account_peppol_edi_status = fields.Selection([
        ('uploaded', 'Uploaded'),
        ('unconfirmed', 'Unconfirmed'),
        ('unpaid', 'Unpaid'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'),
        ('directly_archived', 'Directly Archived'),
        ('will_not_be_paid', 'Will Not Be Paid'),
        ('print_and_post_ready', 'Print and Post Ready'),
        ('delivery_pending', 'Delivery Pending'),
        ('delivery_requested', 'Delivery Requested'),
        ('delivery_failed', 'Delivery Failed'),
        ('validation_failed', 'Validation Failed'),
        ('archiving', 'Archiving'),
        ('to_be_archived', 'Archived'),
        ('incoming', 'Incoming'),
        ('recycle_bin', 'Recycle Bin'),
    ], string="PEPPOL Status", copy=False, tracking=True, help="""
        uploaded: the sales invoice is being processed
        unconfirmed: status when the sales invoice is not sent to customer, can update sales invoices information
        unpaid: the sales invoice is not paid (amount_paid = 0)
        partially_paid: part of the sales invoice is paid (amount_paid < amount)
        paid: the sales invoice is fully paid (amount_paid = amount)
        directly_archived: the invoice is archived (deleted) mannually on BanqUP UI
        will_not_be_paid: the invoice is marked as 'will not be paid' on BanqUP UI
        print_and_post_ready: the sales invoice was delivered to the print partner and is waiting to be print
        delivery_requested: the sales invoice is currently being sent to the customer
        delivery_pending: the invoice is in the process of being delivered
        delivery_failed: an attempt to send the invoice was made but it failed
        validation_failed: the uploaded invoice failed the initial validation
        accountant: the invoice is sent to the accountant and is waiting for approval
        archiving: the invoice is being archived
        to_be_archived: the invoice is received via Archive Connector, but couldn't be archived directly
        incoming: waiting for fitekin approval
        recycle_bin: the invoice is deleted
    """)
    peppol_endpoint = fields.Char(string="PEPPOL ID", related='partner_id.peppol_endpoint', tracking=True, copy=False)
    peppol_sales_invoice_id = fields.Char(string="Invoice ID", copy=False, tracking=True)
    peppol_sales_invoice_uuid = fields.Char(string="PEPPOL Invoice UUID", copy=False, tracking=True)
    is_send_via_peppol = fields.Boolean('Sent via PEPPOL?', copy=False, tracking=True)
    is_enable_peppol = fields.Boolean(string="Enable PEPPOL E-Invoicing", compute="_compute_is_enable_peppol",
                                      copy=False)

    @api.depends('company_id.is_enable_peppol')
    def _compute_is_enable_peppol(self):
        if (self.company_id.is_enable_peppol or self.env.company.is_enable_peppol) and (self.company_id.account_peppol_verification_status == 'verified' or self.env.company.account_peppol_verification_status == 'verified'):
            self.is_enable_peppol = self.company_id.is_enable_peppol
        else:
            self.is_enable_peppol = False

    def _get_account_peppol_edi_url(self):
        # PROD_URL = self.env['ir.config_parameter'].sudo().get_param('xe_account_peppol.url') or False
        url = self.env.company.account_peppol_edi_url
        if not url:
            raise AccessError("Sorry, PEPPOL URL is not set in the system. Please contact your administrator.")
        return url

    def action_create_invoice_on_peppol(self):
        ''' This method is to create an invoice on the PEPPOL Network '''
        endpoint = "/api/v1/invoice/create"
        self.action_create_invoice(endpoint)

    def action_create_credit_note(self):
        ''' This method is to create a credit note on the PEPPOL Network '''
        endpoint = "/api/v1/creditnote/create"
        self.action_create_invoice(endpoint)

    def action_create_invoice(self, endpoint):
        '''
        This method is to create an invoice or a credit note on the PEPPOL Network.
        :param endpoint: endpoint to call for creating invoice/credit note
        :return: Create the invoice/credit note on the PEPPOL and update the PEPPOL Status, Invoice ID & Invoice UUID
        '''
        self._check_field_constrains()
        url = self._get_account_peppol_edi_url()
        payload = self._get_invoice_payload()
        try:
            client_number = self.company_id.client_number or self.env.user.company_id.client_number
            HEADERS['x-client-number'] = client_number
            response = self._make_request(
                f"{url}{endpoint}",
                payload=payload, headers=HEADERS, method="POST"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                if json_response.get('message'):
                    raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            self.account_peppol_edi_status = json_response['status']
            self.peppol_sales_invoice_id = json_response['id']
            self.peppol_sales_invoice_uuid = json_response['sales_invoice_uuid']

        log_message = _('Invoice has been created on PEPPOL Access Point.')
        self._message_log(body=log_message)

    def action_get_account_peppol_edi_status(self):
        '''
        This method is to fetch the updated status for a particular invoice/credit note.
        :return: Updates the PEPPOL status in invoice/credit note
        '''
        if not self.peppol_sales_invoice_id:
            raise ValidationError("No PEPPOL Invoice ID Found!")
        peppol_invoice_id = self.peppol_sales_invoice_id
        self.get_peppol_invoice_status(peppol_invoice_id)

    def action_get_all_account_peppol_edi_status(self):
        '''
        This method is to fetch the updated status for all invoices/credit notes.
        :return: Updates the PEPPOL status in invoices/credit notes
        '''
        account_move = self.env['account.move'].search([('peppol_sales_invoice_id', '!=', False)])
        for invoice_id in account_move:
            peppol_invoice_id = invoice_id.peppol_sales_invoice_id
            invoice_id.get_peppol_invoice_status(peppol_invoice_id)

    def get_peppol_invoice_status(self, peppol_invoice_id):
        '''
        This method is to call the endpoint to fetch the updated status for invoices/credit notes.
        :param peppol_invoice_id: PEPPOL Invoice ID of an invoice created on Odoo
        :return: Updates the PEPPOL status in invoice/credit note
        '''
        url = self._get_account_peppol_edi_url()
        try:
            response = self._make_request(
                f"{url}/api/v1/invoice/detail?invoiceId={peppol_invoice_id}",
                payload={}, headers=HEADERS, method="GET"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                if json_response.get('message'):
                    raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            self.account_peppol_edi_status = json_response['status']

    def action_send_via_peppol(self):
        '''
        This method is to send the invoice to the PEPPOL Network for processing.
        :return: Sends the invoice to the PEPPOL Network and set the is_send_via_peppol flag true
        '''
        payload = {"type": "SEND", "invoiceId": int(self.peppol_sales_invoice_id)}
        invoice_send_response = self.action_update_peppol_invoice_status(payload)
        if invoice_send_response.status_code == 201:
            log_message = _(f"Invoice has been sent to the PEPPOL Access Point for processing.")
            self.is_send_via_peppol = True
            self._message_log(body=log_message)

    def action_create_payment(self, payment_date):
        '''
        This method is to create the payment for an invoice on the PEPPOL Network
        :param payment_date: date of payment
        :return: Updates the payment status of an invoice
        '''
        payload = {
            "invoiceId": int(self.peppol_sales_invoice_id),
            "payload": {
                "payment_amount_paid": self.amount_total - self.amount_residual,
                "payment_date": payment_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            }
        }
        if (self.amount_total - self.amount_residual) > 0:
            payload["type"] = "MARK_AS_PARTIALLY_PAID"
        else:
            payload["type"] = "MARK_AS_PAID"

        self.action_update_peppol_invoice_status(payload)

    def action_update_peppol_invoice_status(self, payload):
        '''
        This method is to fetch the status of the invoice while sending/creating payment
        :param payload: dict of the fields required to be sent
        :return: Updates the Sales Invoice UUID
        '''
        url = self._get_account_peppol_edi_url()
        try:
            response = self._make_request(
                f"{url}/api/v1/invoice/update/status",
                payload=payload, headers=HEADERS, method="POST")
            json_response = json.loads(response.text)
            print(json_response)
            if not (200 <= response.status_code <= 299):
                raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            if json_response.get('sales_invoice_uuid') and self.peppol_sales_invoice_uuid != json_response.get(
                    'sales_invoice_uuid'):
                self.peppol_sales_invoice_uuid = json_response['sales_invoice_uuid']
        return response

    def _make_request(self, url, payload=None, headers=None, method=None):
        account_peppol_edi_access_token = self.company_id.account_peppol_edi_access_token or self.env.user.company_id.account_peppol_edi_access_token
        headers['Authorization'] = f'Bearer {account_peppol_edi_access_token}'
        try:
            response = requests.request(method, url, headers=headers, data=json.dumps(payload))
            _logger.info(f'response --------- {url, headers, payload, response}')
        except Exception as e:
            raise AccessError(e)
        if response.status_code == 401:
            self.env['res.config.settings'].action_regenerate_tokens(self.env.user.company_id)
            print('Token Regenerate----------------------------------------')
            response = self._make_request(url, payload, headers, method)
            print('Recursive Request Call----------------------------------')
        return response

    def _get_invoice_payload(self):
        payload = {
            "sales_invoice_number": self.name,
            "client_number": int(self.company_id.client_number or self.env.user.company_id.client_number),
            "platform_id": 15,
            "currency_code": self.currency_id.name,
            "sales_invoice_date": self.invoice_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "sales_invoice_due_date": self.invoice_date_due.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "delivery_channel": "openpeppol",
            "debtor_id": self.partner_id.debtor_id,
            "client_id": self.partner_id.client_id,
            "invoice_lines": [],
        }
        for line in self.invoice_line_ids:
            payload['invoice_lines'].append({
                "id": line.id,
                "service_name": line.product_id.name,
                "service_description": line.name,
                "service_quantity": line.quantity,
                "service_price": line.price_unit,
                "service_vat": line.price_total - line.price_subtotal,
                "service_subtotal": line.price_subtotal,
                "service_discount_perc": 0,
                "service_discount": 0,
                "service_unit": "Unit"
            })
        return payload

    def _check_field_constrains(self):
        if not self.invoice_date:
            raise ValidationError('Warning! You must enter the "Invoice Date" before sending via peppol.')
        if not self.invoice_date_due:
            raise ValidationError('Warning! You must enter the "Invoice Date" before sending via peppol.')
        if not (self.company_id.client_number or self.env.user.company_id.client_number):
            raise ValidationError(
                f'Warning! Your company "{self.env.user.company_id.name} does not have Client Number.')

    def action_receive_purchase_invoices(self):
        ''' This method is to fetch the received invoices from the PEPPOL Network '''
        api = "/api/v1/invoice/purchase"
        self._make_creditor_requests(api)

    def action_get_creditor(self):
        ''' This method is to create a creditor on Odoo from the PEPPOL Network '''
        api = "/api/v1/creditor"
        self._make_creditor_requests(api)

    def _make_creditor_requests(self, api):
        all_results = []
        page, page_size = 0, 49
        url = self._get_account_peppol_edi_url()
        while True:
            try:
                url = f"{url}{api}?client_number={self.company_id.client_number or self.env.user.company_id.client_number}&page={page}&size={page_size}"
                response = requests.get(url, headers=HEADERS)
                if response.status_code != 200:
                    raise AccessError(f"Error occurred: {response.text}")
                result = response.json().get("results")
                all_results.extend(result)
                if len(result) < 10:
                    break
                page += 1
            except Exception as e:
                raise AccessError(e)
        if 'purchase' in api:
            self.action_create_vendor_bill(all_results)
        else:
            self.action_create_creditor(all_results)
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_create_vendor_bill(self, all_results):
        for data in all_results:
            invoice_id = self.env['account.move'].search([('peppol_sales_invoice_id', '=', data['id'])], limit=1)
            if invoice_id:
                invoice_id.account_peppol_edi_status = data['status']
            else:
                partner_id = self.env['res.partner'].search([('creditor_id', '=', data['creditor_id'])], limit=1)
                creditor_id = data['creditor_id']
                if not partner_id and creditor_id != None:
                    partner_id = self.get_creditor_details(creditor_id)
                bill_data = {
                    "name": data['purchase_invoice_number'],
                    "partner_id": partner_id.id,
                    "invoice_date": data['purchase_invoice_date'],
                    "invoice_date_due": data['purchase_invoice_due_date'],
                    "peppol_sales_invoice_uuid": data['purchase_invoice_uuid'],
                    "peppol_sales_invoice_id": data['id'],
                    "account_peppol_edi_status": data['status'],
                    "move_type": 'in_invoice',
                    "invoice_line_ids": [
                        (0, 0, {
                            "product_id": self.env['product.template'].search([('name', '=', line['service_name'])],
                                                                              limit=1).id,
                            "name": line['service_description'],
                            "quantity": line['service_quantity'] or 0.00,
                            "price_unit": line['service_price'] or 0.00,
                        }) for line in data['invoice_lines']
                    ]
                }
                vendor_bill = self.env['account.move'].create(bill_data)
                return vendor_bill

    def action_create_creditor(self, data):
        partner_uen_check = self.env['res.partner'].search(
            [('l10n_sg_unique_entity_number', '=', data['legal_entity_trn'])], limit=1)
        if not partner_uen_check:
            creditor = {
                "name": data['name'],
                "creditor_id": data['id'],
                "creditor_number": data['creditor_number'],
                "supplier_rank": 1,
                "country_id": self.env['res.country'].search([('code', '=', data['country_code'])]).id,
                "client_id": data['client_id'],
                "street": data['address'],
                "zip": data['zip_code'] or '',
                "city": data['city'] or '',
                "l10n_sg_unique_entity_number": data['legal_entity_trn'] or '',
                "state_id": self.env['res.country.state'].search([('name', '=', data['state'])]).id,
                "email": data['email'] or '',
            }
            partner_id = self.env['res.partner'].create(creditor)
            return partner_id
        else:
            return partner_uen_check

    def get_creditor_details(self, creditor_id):
        url = self._get_account_peppol_edi_url()
        try:
            response = self._make_request(
                f"{url}/api/v1/creditors/{creditor_id}",
                payload={}, headers=HEADERS, method="GET"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        else:
            data = json_response
            create_creditor = self.action_create_creditor(data)
            return create_creditor


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        result = super(AccountPaymentRegister, self)._create_payments()
        moves = self.env['account.move'].browse(self.env.context.get('active_ids'))
        payment_date = result.date
        for move in moves:
            if move.peppol_sales_invoice_id:
                if move.account_peppol_edi_status not in ('unpaid', 'partially_paid'):
                    raise ValidationError(
                        f'Sorry, you can not make the payment for "{move.display_name}" as PEPPOL status is not up to date. Please update the PEPPOL status.')
                else:
                    move.action_create_payment(payment_date)
        return result


class AccountMoveReversal(models.TransientModel):
    _inherit = 'account.move.reversal'

    def reverse_moves(self):
        result = super(AccountMoveReversal, self).reverse_moves()
        # invoice_id = self.env['account.move'].search([('reversed_entry_id', '=', self.env.context.get('active_id'))],
        #                                              limit=1)
        # if invoice_id.peppol_sales_invoice_id and self.refund_method:
        #     invoice_id.action_create_credit_note()
        return result
