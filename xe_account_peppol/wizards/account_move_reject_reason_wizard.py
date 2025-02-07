from odoo import api, fields, models, _


class MoveRejectReasonWizard(models.TransientModel):
    _name = "account.move.reject.reason.wizard"
    _description = "Account Move Reject Reason"

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
    ], string="PEPPOL Reject Status", help="""
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
    reject_reason = fields.Text(string='Reject Reason')

    def action_reject(self):
        if self.env.context.get('active_model') == 'account.move':
            move = self.env[self.env.context.get('active_model')].browse(self.env.context.get('active_id'))
            move.account_peppol_edi_reject_status = self.account_peppol_edi_reject_status
            move.reject_reason = self.reject_reason
            move.action_dispatch_vendor_bill(type='REFUSE', reject_reason=self.reject_reason, reject_status_code=self.account_peppol_edi_reject_status)
            move.with_context(is_skip_reject_reason=True).button_cancel()
