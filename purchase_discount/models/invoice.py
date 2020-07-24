# -*- coding: utf-8 -*-

from odoo import models, fields, api


class Move(models.Model):
    _inherit = 'account.move'

    global_discount = fields.Float()
    total_discount = fields.Float(compute="_compute_total_discount", readonly=True)

    def post(self):
        for m in self.filtered(lambda x: x.type == 'in_invoice' and x.global_discount > 0.0):
            create_vals = {
                            'name': 'Purchase Discount Line',
                            'price_unit': -m.global_discount,
                            'exclude_from_invoice_tab': True,
                            'account_id': self.env.ref('purchase_discount.account_purchase_discount').id,
                            'move_id': m.id,
                        }
            m.write({'invoice_line_ids': [(0, 0, create_vals)]})
        return super(Move, self).post()

    @api.depends('invoice_line_ids.discount', 'invoice_line_ids.fixed_discount', 'global_discount')
    def _compute_total_discount(self):
        for rec in self:
            rec.total_discount = rec.global_discount + sum(rec.invoice_line_ids.mapped('fixed_discount')) + sum(
                rec.invoice_line_ids.mapped(
                    lambda l: (l.price_subtotal / ((100.0 - l.discount) or 1) * 100.0) - l.price_subtotal))

    @api.depends(
        'line_ids.debit',
        'line_ids.credit',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state', 'global_discount')
    def _compute_amount(self):
        invoice_ids = [move.id for move in self if move.id and move.is_invoice(include_receipts=True)]
        self.env['account.payment'].flush(['state'])
        if invoice_ids:
            self._cr.execute(
                '''
                    SELECT move.id
                    FROM account_move move
                    JOIN account_move_line line ON line.move_id = move.id
                    JOIN account_partial_reconcile part ON part.debit_move_id = line.id OR part.credit_move_id = line.id
                    JOIN account_move_line rec_line ON
                        (rec_line.id = part.debit_move_id AND line.id = part.credit_move_id)
                    JOIN account_payment payment ON payment.id = rec_line.payment_id
                    JOIN account_journal journal ON journal.id = rec_line.journal_id
                    WHERE payment.state IN ('posted', 'sent')
                    AND journal.post_at = 'bank_rec'
                    AND move.id IN %s
                UNION
                    SELECT move.id
                    FROM account_move move
                    JOIN account_move_line line ON line.move_id = move.id
                    JOIN account_partial_reconcile part ON part.debit_move_id = line.id OR part.credit_move_id = line.id
                    JOIN account_move_line rec_line ON
                        (rec_line.id = part.credit_move_id AND line.id = part.debit_move_id)
                    JOIN account_payment payment ON payment.id = rec_line.payment_id
                    JOIN account_journal journal ON journal.id = rec_line.journal_id
                    WHERE payment.state IN ('posted', 'sent')
                    AND journal.post_at = 'bank_rec'
                    AND move.id IN %s
                ''', [tuple(invoice_ids), tuple(invoice_ids)]
            )
            in_payment_set = set(res[0] for res in self._cr.fetchall())
        else:
            in_payment_set = {}

        for move in self:
            total_untaxed = 0.0
            total_untaxed_currency = 0.0
            total_tax = 0.0
            total_tax_currency = 0.0
            total_residual = 0.0
            total_residual_currency = 0.0
            total = 0.0
            total_currency = 0.0
            currencies = set()
            for line in move.line_ids:
                if line.currency_id:
                    currencies.add(line.currency_id)

                if move.is_invoice(include_receipts=True):
                    # === Invoices ===

                    if not line.exclude_from_invoice_tab:
                        # Untaxed amount.
                        total_untaxed += line.balance
                        total_untaxed_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.tax_line_id:
                        # Tax amount.
                        total_tax += line.balance
                        total_tax_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.account_id.user_type_id.type in ('receivable', 'payable'):
                        # Residual amount.
                        total_residual += line.amount_residual
                        total_residual_currency += line.amount_residual_currency
                else:
                    # === Miscellaneous journal entry ===
                    if line.debit:
                        total += line.balance
                        total_currency += line.amount_currency

            if move.type == 'entry' or move.is_outbound():
                sign = 1
            else:
                sign = -1
            move.amount_untaxed = sign * (total_untaxed_currency if len(currencies) == 1 else total_untaxed) - move.global_discount
            move.amount_tax = sign * (total_tax_currency if len(currencies) == 1 else total_tax)
            move.amount_total = sign * (total_currency if len(currencies) == 1 else total) - move.global_discount
            move.amount_residual = -sign * (total_residual_currency if len(currencies) == 1 else total_residual)
            move.amount_untaxed_signed = -total_untaxed
            move.amount_tax_signed = -total_tax
            move.amount_total_signed = abs(total) if move.type == 'entry' else -total
            move.amount_residual_signed = total_residual

            currency = len(currencies) == 1 and currencies.pop() or move.company_id.currency_id
            is_paid = currency and currency.is_zero(move.amount_residual) or not move.amount_residual

            # Compute 'invoice_payment_state'.
            if move.type == 'entry':
                move.invoice_payment_state = False
            elif move.state == 'posted' and is_paid:
                if move.id in in_payment_set:
                    move.invoice_payment_state = 'in_payment'
                else:
                    move.invoice_payment_state = 'paid'
            else:
                move.invoice_payment_state = 'not_paid'

    # Load all unsold PO lines
    @api.onchange('purchase_id')
    def _onchange_purchase_auto_complete(self):
        self.global_discount = self.purchase_id.global_discount
        return super(Move, self)._onchange_purchase_auto_complete()

    def discount_wizard_invoice(self):
        discount_wizard = self.env['purchase.discount.wizard'].create({'invoice_id': self.id})
        return {
            'name': 'Discount Whole Invoice',
            'binding_views': 'form',
            'view_mode': 'form',
            'res_model': 'purchase.discount.wizard',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': discount_wizard.id,
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    fixed_discount = fields.Float(string='Fixed Discount', digits='Discount', default=0.0)
    new_price_unit = fields.Float(string='New Price', digits='Product Price')  # temporary use because of we can not override the create method

    @api.onchange('price_unit')
    def _onchange_price_unit_to_new_price_unit(self):
        # issue of
        # case 1:  Po to create BIll
        # price : 10, fixed_discount: 1, subtotal : 9
        # after click on the save button
        # price : 9, fixed_discount: 1 , subtotal : 9
        # same issue when created the bill with fixdiscount
        for l in self:
            l.new_price_unit = l.price_unit

    def write(self, vals):
        # to avoid blocking the write because of readonly when changing from one type of discount to the other
        if 'fixed_discount' in vals and vals.get('fixed_discount') > 0:
            vals['discount'] = 0
        if 'discount' in vals and vals.get('discount') > 0:
            vals['fixed_discount'] = 0

        return super(AccountMoveLine, self).write(vals)

    @api.onchange('product_id')
    def _get_discount_onproductchange(self):
        if self.move_id.partner_id and self.move_id.type == 'in_invoice':
            partner = self.move_id.partner_id.commercial_partner_id or self.move_id.partner_id
            vendor_price = self.env['product.supplierinfo'].search(
                [('name', '=', partner.id), '|', ('product_tmpl_id', '=', self.product_id.product_tmpl_id.id),
                 ('product_tmpl_id', '=', self.product_id.id)], limit=1)
            if vendor_price:
                amount = self.price_unit or self.product_id.standard_price
                if vendor_price.fixed_discount and vendor_price.fixed_discount < amount:
                    self.fixed_discount = vendor_price.fixed_discount
                elif vendor_price.percent_discount:
                    self.discount = vendor_price.percent_discount

    def _get_price_total_and_subtotal(self, price_unit=None, quantity=None, discount=None, currency=None, product=None, partner=None, taxes=None, move_type=None):
        self.ensure_one()
        res = super(AccountMoveLine, self)._get_price_total_and_subtotal(price_unit, quantity, discount, currency, product, partner, taxes, move_type)
        if self.fixed_discount > 0.0 and not discount and self.move_id.type == 'in_invoice':
            res['price_total'] = res['price_total'] - self.fixed_discount
            res['price_subtotal'] = res['price_subtotal'] - self.fixed_discount
        return res

    @api.onchange('quantity', 'discount', 'price_unit', 'tax_ids', 'fixed_discount')
    def _onchange_price_subtotal(self):
        return super(AccountMoveLine, self)._onchange_price_subtotal()

    def _copy_data_extend_business_fields(self, values):
        # OVERRIDE to copy the 'fixed_discount, new_price_unit' field as well.
        if self.move_id.type == 'in_invoice':
            values['fixed_discount'] = self.fixed_discount
            values['new_price_unit'] = self.price_unit
        super(AccountMoveLine, self)._copy_data_extend_business_fields(values)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super(AccountMoveLine, self).create(vals_list)
        # issue of
        # case 1:  Po to create BIll ()
        # price : 10, fixed_discount: 1, subtotal : 9
        # after click on the save button
        # price : 9, fixed_discount: 1 , subtotal : 9
        # same issue when created the bill with fixdiscount
        move_id = lines.mapped('move_id')
        if move_id.type == 'in_invoice':
            for line in lines:
                if line.new_price_unit > 0.0 and line.fixed_discount > 0.0:
                    line.price_unit = line.new_price_unit
            return lines
