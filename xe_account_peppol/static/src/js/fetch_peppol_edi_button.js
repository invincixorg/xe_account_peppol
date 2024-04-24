odoo.define('xe_account_peppol.fetch_peppol_edi_button', function (require) {
"use strict";

    var ListController = require('web.ListController');
    var ListView = require('web.ListView');
    var viewRegistry = require('web.view_registry');
    var rpc = require('web.rpc');
    var session = require('web.session');

    var InvoicesTreeButton = ListController.extend({
        buttons_template: 'xe_account_peppol.fetch_peppol_invoices_btn',
        events: _.extend({}, ListController.prototype.events, {
            'click .get_sales_invoice': '_SentInvoices',
        }),

        willStart() {
            return Promise.all([this._super(...arguments), this._HideSentPeppolBtn(), this._SetEnablePeppol()]);
        },

        _SentInvoices:function (){
            var self = this;
            self._rpc({
                model:'account.move',
                method:'action_get_all_account_peppol_edi_status',
                args:[1],
            }).then(function (result) {
                location.reload();
            });
        },

        _HideSentPeppolBtn: function () {
            session.user_has_group('xe_account_peppol.group_peppol_invoice').then(hasGroup => {
                this.isEnableSentInvoices = hasGroup;
            });
        },

        _SetEnablePeppol: function (){
            var self = this;
            var isEnablePeppol = self._rpc({
                model:'res.company',
                method:'get_is_peppol_enabled',
                args: [[]],
            })
            .then(function (result) {
                self.isEnablePeppol = result;
            });
            return $.when(isEnablePeppol);
        },
    });

    var InvoicesListView = ListView.extend({
        config: _.extend({}, ListView.prototype.config, {
            Controller: InvoicesTreeButton,
        }),
    });

    var BillTreeButton = ListController.extend({
        buttons_template: 'xe_account_peppol.fetch_peppol_bills_btn',
        events: _.extend({}, ListController.prototype.events, {
            'click .get_purchase_invoice': '_ReceivedInvoices',
        }),

        willStart() {
            return Promise.all([this._super(...arguments), this._HideReceivedInvoicesBtn(), this._SetEnablePeppol()]);
        },

        _ReceivedInvoices:function (){
            var self = this;
                self._rpc({
                model:'account.move',
                method:'action_receive_purchase_invoices',
                args:[1],
            }).then(function (result) {
                location.reload();
            });
        },

        _HideReceivedInvoicesBtn: function () {
            session.user_has_group('xe_account_peppol.group_peppol_invoice').then(hasGroup => {
                this.isEnableReceivedInvoices = hasGroup;
            });
        },

        _SetEnablePeppol: function (){
            var self = this;
            var isEnablePeppol = self._rpc({
                model:'res.company',
                method:'get_is_peppol_enabled',
                args: [[]],
            })
            .then(function (result) {
                self.isEnablePeppol = result;
            });
            return $.when(isEnablePeppol);
        },
    });

    var BillsListView = ListView.extend({
        config: _.extend({}, ListView.prototype.config, {
            Controller: BillTreeButton,
        }),
    });

    viewRegistry.add('fetch_invoices_tree_btn', InvoicesListView);
    viewRegistry.add('fetch_bills_tree_btn', BillsListView);
});