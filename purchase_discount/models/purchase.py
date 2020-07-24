# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    fixed_discount = fields.Float()
    percent_discount = fields.Float(string="Discount (%)")

    def write(self, vals):
        # to avoid blocking the write because of readonly when changing from one type of discount to the other
        if vals.get('fixed_discount', 0):
            vals['percent_discount'] = 0
        elif vals.get('percent_discount', 0):
            vals['fixed_discount'] = 0
        return super(PurchaseOrderLine, self).write(vals)

    @api.depends('product_qty', 'price_unit', 'taxes_id', 'fixed_discount', 'percent_discount')
    def _compute_amount(self):
        for line in self:
            vals = line._prepare_compute_all_values()
            if line.percent_discount:
                vals['price_unit'] = vals['price_unit'] * (1 - (line.percent_discount or 0.0) / 100.0)

            taxes = line.taxes_id.compute_all(
                vals['price_unit'],
                vals['currency_id'],
                vals['product_qty'],
                vals['product'],
                vals['partner'])

            subtotal = taxes['total_excluded']
            if line.fixed_discount:
                subtotal -= line.fixed_discount

            taxes_amount = sum(t.get('amount', 0.0) for t in taxes.get('taxes', []))
            line.update({
                'price_tax': taxes_amount,
                'price_total': subtotal + taxes_amount,
                'price_subtotal': subtotal,
            })

    @api.onchange('product_id')
    def get_discount_onproductchange(self):
        if self.order_id.partner_id:
            partner = self.order_id.partner_id.commercial_partner_id or self.order_id.partner_id
            vendor_price = self.env['product.supplierinfo'].search([('name', '=', partner.id), '|', ('product_tmpl_id', '=', self.product_id.product_tmpl_id.id), ('product_tmpl_id', '=', self.product_id.id)], limit=1)
            if vendor_price:
                if vendor_price.fixed_discount:
                    self.fixed_discount = vendor_price.fixed_discount
                elif vendor_price.percent_discount:
                    self.percent_discount = vendor_price.percent_discount

    @api.model
    def create(self, values):
        res = super(PurchaseOrderLine, self).create(values)
        if not res.fixed_discount and not res.percent_discount:
            res.get_discount_onproductchange()
        return res

    def _prepare_account_move_line(self, move):
        res = super(PurchaseOrderLine, self)._prepare_account_move_line(move)
        if self.percent_discount > 0.0:
            res['discount'] = self.percent_discount
        elif self.fixed_discount > 0.0:
            res['fixed_discount'] = self.fixed_discount
            res['fixed_discount'] = self.fixed_discount
            res['new_price_unit'] = self.price_unit
        return res


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    global_discount = fields.Float(string='Global Discount (After Taxes)')
    total_discount = fields.Float(compute="_compute_total_discount", readonly=True)

    @api.depends('order_line.percent_discount', 'order_line.fixed_discount', 'global_discount')
    def _compute_total_discount(self):
        for rec in self:
            rec.total_discount = rec.global_discount + sum(rec.order_line.mapped('fixed_discount')) + sum(rec.order_line.mapped(lambda l: (l.price_subtotal / ((100.0 - l.percent_discount) or 1) * 100.0) - l.price_subtotal))

    @api.onchange('partner_id')
    def get_discount_onpartnerchange(self):
        for line in self.order_line:
            vendor_price = self.env['product.supplierinfo'].search([('name', '=', self.partner_id.id), '|', ('product_tmpl_id', '=', line.product_id.product_tmpl_id.id), ('product_tmpl_id', '=', line.product_id.id)], limit=1)
            if vendor_price:
                if vendor_price.fixed_discount:
                    line.fixed_discount = vendor_price.fixed_discount
                elif vendor_price.percent_discount:
                    line.percent_discount = vendor_price.percent_discount

    def discount_wizard_purchase(self):
        discount_wizard = self.env['purchase.discount.wizard'].create({'purchase_id': self.id})
        return {
            'name': 'Discount Whole Order',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'purchase.discount.wizard',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': discount_wizard.id,
        }

    @api.depends('order_line.price_total', 'global_discount')
    def _amount_all(self):
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax

            amount_total = amount_untaxed + amount_tax
            if order.global_discount:
                amount_total -= order.global_discount
            order.update({
                'amount_untaxed': order.currency_id.round(amount_untaxed),
                'amount_tax': order.currency_id.round(amount_tax),
                'amount_total': amount_total,
            })


class ProductSupplierInfo(models.Model):
    _inherit = 'product.supplierinfo'

    fixed_discount = fields.Float()
    percent_discount = fields.Float(string="Discount (%)")
