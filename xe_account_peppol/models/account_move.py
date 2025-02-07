from odoo import fields, models, api, _, tools, Command
from odoo.exceptions import AccessError, ValidationError

import requests
import base64
import mimetypes
import json
import logging

_logger = logging.getLogger(__name__)

HEADERS = {
    'Content-Type': 'application/json',
}


class AccountMove(models.Model):
    _inherit = 'account.move'

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
    reject_reason = fields.Text(string='Reject Reason', tracking=True)
    account_peppol_edi_reject_status = fields.Selection([
        ('NON', 'NON - No Issue'),
        ('REF', 'REF - References incorrect'),
        ('LEG', 'LEG - Legal information incorrect'),
        ('REC', 'REC - Receiver unknown'),
        ('QUA', 'QUA - Item quality insufficient'),
        ('DEL', 'DEL - Delivery issues'),
        ('PRI', 'PRI - Prices incorrect'),
        ('QTY', 'QTY - Quantity incorrect'),
        ('ITM', 'ITM - Items incorrect'),
        ('PAY', 'PAY - Payment terms incorrect'),
        ('UNR', 'UNR - Not recognized'),
        ('FIN', 'FIN - Finance incorrect'),
        ('PPD', 'PPD - Partially Paid'),
        ('OTH', 'OTH - Other'),
    ], string="PEPPOL Reject Status", copy=False, tracking=True, help="""
        NON - No Issue: Indicates that receiver of the documents sends the message just to update the status and there are no problems with document processing
        REF - References incorrect: Indicates that the received document did not contain references as required by the receiver for correctly routing the document for approval or processing
        LEG - Legal information incorrect: Information in the received document is not according to legal requirements
        REC - Receiver unknown: The party to which the document is addressed is not known
        QUA - Item quality insufficient: Unacceptable or incorrect quality
        DEL - Delivery issues: Delivery proposed or provided is not acceptable
        PRI - Prices incorrect: Prices not according to previous expectation
        QTY - Quantity incorrect: Quantity not according to previous expectation
        ITM - Items incorrect: Items not according to previous expectation
        PAY - Payment terms incorrect: Payment terms not according to previous expectation
        UNR - Not recognized: Commercial transaction not recognized
        FIN - Finance incorrect: Finance terms not according to previous expectation
        PPD - Partially Paid: Payment is partially but not fully paid
        OTH - Other: Reason for status is not defined by code
    """)
    attachment_ids = fields.Many2many('ir.attachment', 'attachment_account_move_rel', 'move_id', 'attach_id',
                                      string='Add Attachments')

    @api.depends('company_id.is_enable_peppol')
    def _compute_is_enable_peppol(self):
        if (self.company_id.is_enable_peppol or self.env.company.is_enable_peppol) and (
                self.company_id.account_peppol_verification_status == 'verified' or self.env.company.account_peppol_verification_status == 'verified'):
            self.is_enable_peppol = self.company_id.is_enable_peppol
        else:
            self.is_enable_peppol = False

    def action_create_invoice_on_peppol(self):
        ''' This method is to create an invoice on the PEPPOL Network '''
        endpoint = "/api/v1/invoice/create"
        log_message = _('Invoice has been created on PEPPOL Access Point.')
        self.action_create_update_invoice(endpoint, invoice_type='invoice', log_message=log_message)

    def action_update_invoice_on_peppol(self):
        ''' This method is to update an invoice on the PEPPOL Network '''
        endpoint = "/api/v1/invoice/update"
        HEADERS['id'] = self.peppol_sales_invoice_id
        log_message = _(f'Invoice has been updated on PEPPOL Access Point.')
        self.action_create_update_invoice(endpoint, invoice_type='invoice', method="PUT", log_message=log_message)

    def action_create_update_invoice(self, endpoint, method="POST", invoice_type=None, log_message=''):
        '''
        This method is to create an invoice or a credit note on the PEPPOL Network.
        :param endpoint: endpoint to call for creating invoice/credit note
        :param method: method to get the endpoint request method
        :param invoice_type: invoice_type to differentiate between invoice/credit note
        :param log_message: log_message to log the message about the operation
        :return: Create the invoice/credit note on the PEPPOL and update the PEPPOL Status, Invoice ID & Invoice UUID
        '''
        self._check_field_constrains()
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        payload = self._get_invoice_payload(invoice_type=invoice_type)
        try:
            client_number = company_id.client_number or company_id.client_number
            HEADERS['x-client-number'] = client_number
            response = company_id._make_request(
                f"{url}{endpoint}",
                payload=payload, headers=HEADERS, method=method
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

        self._message_log(body=log_message)

    def action_send_attachments(self):
        '''
        This method is to upload attachment for an invoice on the PEPPOL Network.
        '''
        if self.attachment_ids:
            company_id = self.company_id or self.env.user.company_id
            url = company_id._get_server_url()
            endpoint = '/api/v1/invoice/sales-invoices/{}/attachments/v2'.format(self.peppol_sales_invoice_id)
            for attachment in self.attachment_ids:
                payload = {
                    "fileName": attachment.name,
                    "mimeType": attachment.mimetype,
                    "fileBase64": attachment.datas.decode('utf-8')
                }
                try:
                    client_number = company_id.client_number or company_id.client_number
                    HEADERS['x-client-number'] = client_number
                    response = company_id._make_request(
                        f"{url}{endpoint}",
                        payload=payload, headers=HEADERS, method="POST"
                    )
                    json_response = json.loads(response.text)
                    if not (200 <= response.status_code <= 299):
                        if json_response.get('message'):
                            raise AccessError(json_response.get('message'))
                except Exception as e:
                    raise AccessError(e)

            self._message_log(body=_(f'Attachments has been updated for this invoice on PEPPOL Access Point.'))

    def action_send_via_peppol(self):
        '''
        This method is to send the invoice to the PEPPOL Network for processing.
        :return: Sends the invoice to the PEPPOL Network and set the is_send_via_peppol flag true
        '''
        # Attaching files to the invoices
        self.action_send_attachments()
        # Sending invoices via PEPPOL
        endpoint = '/api/v1/invoice/update/status'
        payload = {"type": "SEND", "invoiceId": int(self.peppol_sales_invoice_id)}
        invoice_send_response = self.action_update_peppol_invoice_status(endpoint=endpoint, payload=payload)
        if invoice_send_response.status_code == 201:
            if self.move_type == 'out_invoice':
                log_message = _(f"Invoice has been sent to the PEPPOL Access Point for processing.")
            elif self.move_type == 'out_refund':
                log_message = _(f"Credit Note has been sent to the PEPPOL Access Point for processing.")
            else:
                log_message = _(f"Document has been sent to the PEPPOL Access Point for processing.")
            self.is_send_via_peppol = True
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
        account_move = self.env['account.move'].search(
            [('peppol_sales_invoice_id', '!=', False), ('move_type', '=', 'out_invoice')])
        for invoice_id in account_move:
            peppol_invoice_id = invoice_id.peppol_sales_invoice_id
            invoice_id.get_peppol_invoice_status(peppol_invoice_id)

    def get_peppol_invoice_status(self, peppol_invoice_id):
        '''
        This method is to call the endpoint to fetch the updated status for invoices/credit notes.
        :param peppol_invoice_id: PEPPOL Invoice ID of an invoice created on Odoo
        :return: Updates the PEPPOL status in invoice/credit note
        '''
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        try:
            response = company_id._make_request(
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

    def action_create_payment(self, payment_date):
        '''
        This method is to create the payment for an invoice on the PEPPOL Network
        :param payment_date: date of payment
        :return: Updates the payment status of an invoice
        '''
        endpoint = '/api/v1/invoice/update/status'
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

        self.action_update_peppol_invoice_status(endpoint=endpoint, payload=payload)

    def action_update_peppol_invoice_status(self, endpoint, payload):
        '''
        This method is to fetch the status of the invoice while sending/creating payment
        :param payload: dict of the fields required to be sent
        :return: Updates the Sales Invoice UUID
        '''
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        try:
            response = company_id._make_request(
                f"{url}{endpoint}",
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

    def action_create_credit_note_on_peppol(self):
        ''' This method is to create a credit note on the PEPPOL Network '''
        endpoint = "/api/v1/invoice/credit-notes"
        log_message = _(f'Credit Note has been created against this invoice on PEPPOL Access Point.')
        self.action_create_update_invoice(endpoint, invoice_type='credit_note', log_message=log_message)

    def action_receive_purchase_invoices(self):
        ''' This method is to fetch the received invoices from the PEPPOL Network '''
        endpoint = "/api/v1/invoice/purchase"
        self._make_creditor_requests(endpoint)

    def action_get_creditor(self):
        ''' This method is to create a creditor on Odoo from the PEPPOL Network '''
        endpoint = "/api/v1/creditor"
        self._make_creditor_requests(endpoint)

    def _make_creditor_requests(self, endpoint):
        all_results = []
        page, page_size = 0, 49
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        while True:
            try:
                response = company_id._make_request(
                    f"{url}{endpoint}?client_number={self.company_id.client_number or self.env.user.company_id.client_number}&page={page}&size={page_size}",
                    payload={}, headers=HEADERS, method="GET")
                json_response = json.loads(response.text)
                print(json_response)
                if not (200 <= response.status_code <= 299):
                    raise AccessError(json_response.get('message'))
                result = response.json().get("results")
                all_results.extend(result)
                if len(result) < 10:
                    break
                page += 1
            except Exception as e:
                raise AccessError(e)
        if 'purchase' in endpoint:
            self.action_create_vendor_bill(all_results)
        else:
            self.action_create_creditor(all_results)
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_create_vendor_bill(self, all_results):
        for data in all_results:
            vendor_bill = self.env['account.move'].search([('peppol_sales_invoice_id', '=', data['id'])], limit=1)
            if vendor_bill:
                vendor_bill.account_peppol_edi_status = data['status']
            else:
                partner_id = self.env['res.partner'].search([('creditor_id', '=', data['creditor_id'])], limit=1)
                creditor_id = data['creditor_id']
                if not partner_id and creditor_id != None:
                    partner_id = self.get_creditor_details(creditor_id)
                currency_id = self.env['res.currency'].search([('name', '=', data['currency_code'])], limit=1)
                bill_data = {
                    "partner_id": partner_id.id,
                    "currency_id": currency_id.id,
                    "ref": data['purchase_invoice_number'],
                    "payment_reference": data['payment_reference'],
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
                # vendor_bill.action_post()
            vendor_bill.action_get_attachments()

    def action_get_attachments(self):
        '''
        This method is to get the attachments for received invoices from PEPPOL Network.
        '''
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        endpoint = '/api/v1/invoice/purchase/{}/attachments'.format(self.peppol_sales_invoice_id)
        try:
            client_number = company_id.client_number or company_id.client_number
            HEADERS['x-client-number'] = client_number
            response = company_id._make_request(
                f"{url}{endpoint}",
                payload={}, headers=HEADERS, method="GET"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                if json_response.get('message'):
                    raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)

        for attachment in json_response.get('attachments'):
            resp = requests.get(attachment['download_url'])
            file_content = base64.b64encode(resp.content)

            # Create the attachment
            attachment = self.env['ir.attachment'].create({
                'name': attachment['file_name'],
                'type': 'binary',
                'datas': file_content,
                'res_model': 'account.move',
                'res_id': self.id,
                'mimetype': mimetypes.guess_type(attachment['file_name'])[0],
            })

            # Link the attachment to the move record
            self.write({'attachment_ids': [(4, attachment.id)]})

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
                "street": data['address'] or '',
                "zip": data['zip_code'] or '',
                "city": data['city'] or '',
                "l10n_sg_unique_entity_number": data['legal_entity_trn'] or '',
                "state_id": self.env['res.country.state'].search([('name', '=', data['state'])]).id,
                "email": data['email'] or '',
                "peppol_endpoint": data['peppol_id'] or '',
            }
            partner_id = self.env['res.partner'].create(creditor)
            return partner_id
        else:
            return partner_uen_check

    def action_dispatch_vendor_bill(self, type=None, reject_reason='', reject_status_code=''):
        '''
        This method is to dispatch the vendor bill action to the PEPPOL Access Point.
        :param type: string to indentify whether it is ACCEPTED/REFUSED
        :param reject_reason: string to specify the reason of rejecting the invoice/bill
        :param reject_status_code: string to specify the status code of the reason
        '''
        endpoint = '/api/v1/invoice/purchase/action'
        # HEADERS['id'] = self.peppol_sales_invoice_id
        payload = {"type": type, "invoiceId": int(self.peppol_sales_invoice_id)}
        response = self.action_update_peppol_invoice_status(endpoint=endpoint, payload=payload)
        if response.status_code == 201:
            self.action_dispatch_vendor_bill_response(type=type, reject_reason=reject_reason,
                                                      reject_status_code=reject_status_code)
            if type == 'ACCEPT':
                log_message = _(f"Vendor Bill has been accepted and status has been sent to the PEPPOL Access Point.")
                self._message_log(body=log_message)
            elif type == 'REFUSE':
                log_message = _(f"Vendor Bill has been rejected and status has been sent to the PEPPOL Access Point.")
                self._message_log(body=log_message)

    def action_dispatch_vendor_bill_response(self, type=None, reject_reason='', reject_status_code=''):
        '''
        This method is to dispatch the vendor bill response to the PEPPOL Access Point
        :param type: string to indentify whether it is ACCEPTED/REFUSED
        :param reject_reason: string to specify the reason of rejecting the invoice/bill
        :param reject_status_code: string to specify the status code of the reason
        :return: returns the response
        '''
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        HEADERS['id'] = self.peppol_sales_invoice_id
        response_code = ''
        if type == 'ACCEPT':
            response_code = 'AP'
        elif type == 'REFUSE':
            response_code = 'RE'
        payload = {
            'response_code': response_code,
            'rejection_reason': reject_reason,
            'status_reason_code': reject_status_code,
        }
        try:
            response = company_id._make_request(
                f"{url}/api/v1/invoice/purchase/invoice-responses",
                payload=payload, headers=HEADERS, method="POST")
            json_response = json.loads(response.text)
            print(json_response)
            if not (200 <= response.status_code <= 299):
                raise AccessError(json_response.get('message'))
        except Exception as e:
            raise AccessError(e)
        return json_response

    def action_open_credit_note(self):
        action = self.env["ir.actions.actions"]._for_xml_id('account.action_move_out_refund_type')
        if len(self.reversal_move_id.ids) == 1:
            action['view_mode'] = 'form'
            action['views'] = [(self.env.ref('account.view_move_form').id, 'form')]
            action['res_id'] = self.reversal_move_id.ids[0]
        else:
            action['domain'] = [('id', 'in', self.reversal_move_id.ids)]
        return action

    def action_post(self):
        if self.move_type == 'in_invoice' and self.peppol_sales_invoice_id:
            self.action_dispatch_vendor_bill(type='ACCEPT')
        super(AccountMove, self).action_post()

    def button_cancel(self):
        if self.move_type == 'in_invoice' and self.peppol_sales_invoice_id and not self.env.context.get('is_skip_reject_reason'):
            return {
                'type': 'ir.actions.act_window',
                'name': 'Reject Reason',
                'res_model': 'account.move.reject.reason.wizard',
                'view_mode': 'form',
                'target': 'new'
            }
        super(AccountMove, self).button_cancel()

    def get_creditor_details(self, creditor_id):
        company_id = self.company_id or self.env.user.company_id
        url = company_id._get_server_url()
        try:
            response = company_id._make_request(
                f"{url}/api/v1/creditors/{creditor_id}",
                payload={}, headers=HEADERS, method="GET"
            )
            json_response = json.loads(response.text)
            if not (200 <= response.status_code <= 299):
                raise AccessError(json_response.get('message'))
            return self.action_create_creditor(json_response)
        except Exception as e:
            raise AccessError(e)

    def _get_invoice_payload(self, invoice_type=''):
        '''
        This method is to construct the payload for the invoice
        :param invoice_type: invoice_type to differentiate between invoice/credit note
        :return: returns the payload of the invoice
        '''
        payload = {
            "sales_invoice_number": self.name,
            "platform_id": 15,
            "currency_code": self.currency_id.name,
            "sales_invoice_date": self.invoice_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "sales_invoice_due_date": self.invoice_date_due.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "delivery_channel": "openpeppol",
            "debtor_id": self.partner_id.debtor_id,
            "client_id": self.partner_id.client_id,
            "invoice_lines": [],
        }
        if invoice_type == 'credit_note':
            payload['original_invoices'] = [{
                'up_doc_ref': self.reversed_entry_id.peppol_sales_invoice_uuid
            }]
        if invoice_type == 'invoice':
            payload['client_number'] = int(self.company_id.client_number or self.env.user.company_id.client_number)
            if self.peppol_sales_invoice_uuid:
                payload['sales_invoice_uuid'] = self.peppol_sales_invoice_uuid
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

    @api.depends('restrict_mode_hash_table', 'state', 'is_enable_peppol', 'account_peppol_edi_status')
    def _compute_show_reset_to_draft_button(self):
        super(AccountMove, self)._compute_show_reset_to_draft_button()
        for move in self:
            move.show_reset_to_draft_button = move.show_reset_to_draft_button and (
                        move.is_enable_peppol and move.account_peppol_edi_status in (False, 'uploaded', 'unconfirmed'))


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
        invoice_id = self.env['account.move'].search([('reversed_entry_id', '=', self.env.context.get('active_id'))],
                                                     limit=1)
        if all(self.move_ids.mapped('peppol_sales_invoice_id')):
            if self.refund_method == 'refund':
                raise ValidationError(
                    "Sorry! You can't do partial refund for the invoice that has been sent to PEPPOL Access Point.")
            else:
                invoice_id.action_create_credit_note_on_peppol()
        return result
