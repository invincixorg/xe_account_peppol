/** @odoo-module **/
import { registry } from "@web/core/registry";
import { preferencesItem } from "@web/webclient/user_menu/user_menu_items";


registry.category("user_menuitems").remove("documentation")
registry.category("user_menuitems").remove("support")
registry.category("user_menuitems").remove("shortcuts")
registry.category("user_menuitems").remove("odoo_account")
