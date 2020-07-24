# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseDiscountWizard(models.TransientModel):
    _name = 'purchase.discount.wizard'
    _description = 'A wizard to add discount in batch on PO lines or invoice lines'

    purchase_id = fields.Many2one('purchase.order')
    invoice_id = fields.Many2one('account.move')
    fixed_discount = fields.Float()
    percent_discount = fields.Float()

    def set_discount(self):
        if self.purchase_id:
            if self.fixed_discount:
                self.purchase_id.order_line.write({'fixed_discount': self.fixed_discount})
            elif self.percent_discount:
                self.purchase_id.order_line.write({'percent_discount': self.percent_discount})
        if self.invoice_id:
            if self.fixed_discount:
                self.invoice_id.invoice_line_ids.write({'fixed_discount': self.fixed_discount})
            elif self.percent_discount:
                self.invoice_id.invoice_line_ids.write({'discount': self.percent_discount})
