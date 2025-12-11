"""
Prompt and instruction builders for OpenAI engine.
"""

import logging

logger = logging.getLogger(__name__)


class PromptsBuilder:
    """Builds instructions and welcome messages from DID config"""
    
    def __init__(self, did_config, customer_name_from_history=None):
        """
        Initialize prompts builder.
        
        Args:
            did_config: DID-specific configuration dict
            customer_name_from_history: Customer name from order history (if available)
        """
        self.did_config = did_config
        self.customer_name_from_history = customer_name_from_history
    
    def get_function_definitions(self):
        """Load function definitions from DID config, with fallback to defaults"""
        default_functions = [
            {"type": "function", "name": "terminate_call",
             "description": "ONLY call this function when the USER explicitly says they want to end the call. "
                            "Examples: 'خداحافظ', 'بای', 'تماس رو قطع کن', 'تماس رو پایان بده'. "
                            "DO NOT call this if: user is silent, user says '.', user pauses, or you just finished talking. "
                            "ONLY call when user EXPLICITLY requests to end the call. "
                            "Always say a friendly goodbye first, then call this function.",
             "parameters": {"type": "object", "properties": {}, "required": []}},
            {"type": "function", "name": "transfer_call",
             "description": "call the function if a request was received to transfer a call with an operator, a person",
             "parameters": {"type": "object", "properties": {}, "required": []}},
        ]
        
        if self.did_config and 'functions' in self.did_config:
            custom_functions = self.did_config['functions']
            if isinstance(custom_functions, list):
                return custom_functions
            elif isinstance(custom_functions, dict):
                function_map = {f['name']: f for f in default_functions}
                for func in custom_functions.values():
                    if isinstance(func, dict) and 'name' in func:
                        function_map[func['name']] = func
                return list(function_map.values())
        
        return default_functions
    
    def get_scenario_config(self, scenario_type):
        """Get scenario configuration from DID config"""
        if not self.did_config:
            return {}
        scenarios = self.did_config.get('scenarios', {})
        return scenarios.get(scenario_type, {})
    
    def build_instructions(self, has_undelivered_order=False, orders=None):
        """Build instructions from DID config, with scenario support"""
        base_instructions_template = ""
        if self.did_config and 'instructions_base' in self.did_config:
            base_instructions_template = self.did_config['instructions_base']
        else:
            base_instructions_template = "شما یک دستیار هوشمند هستید. فقط فارسی صحبت کنید. لحن: گرم، پرانرژی، مودب، حرفه‌ای."
        
        name_instruction = ""
        if self.customer_name_from_history:
            name_instruction = f"مهم: نام مشتری ({self.customer_name_from_history}) از تاریخچه در دسترس است. نیازی به پرسیدن نام نیست. "
        else:
            name_instruction = "اگر نام مشتری موجود نیست، نام را بپرسید. "
        
        base_instructions = base_instructions_template.replace("{name_instruction}", name_instruction)
        
        scenario_type = 'has_orders' if has_undelivered_order else 'new_customer'
        scenario_config = self.get_scenario_config(scenario_type)
        
        scenario_instructions = ""
        if scenario_config:
            if has_undelivered_order and orders:
                if len(orders) == 1:
                    template = scenario_config.get('single_order_template', "")
                    if template:
                        order = orders[0]
                        scenario_instructions = template.replace("{status_display}", str(order.get('status_display', '')))
                else:
                    template = scenario_config.get('multiple_orders_template', "")
                    if template:
                        scenario_instructions = template.replace("{orders_count}", str(len(orders)))
            else:
                template = scenario_config.get('new_order_template', "")
                if template:
                    scenario_instructions = template.replace("{name_instruction}", name_instruction)
        
        if scenario_instructions:
            return base_instructions + " " + scenario_instructions
        return base_instructions
    
    def build_welcome_message(self, has_undelivered_order=False, orders=None):
        """Build welcome message from DID config"""
        service_name = (self.did_config.get('restaurant_name') if self.did_config else None) or \
                      (self.did_config.get('service_name') if self.did_config else None) or \
                      'خدمات ما'
        
        scenario_type = 'has_orders' if has_undelivered_order else 'new_customer'
        scenario_config = self.get_scenario_config(scenario_type)
        welcome_templates = scenario_config.get('welcome_templates', {}) if scenario_config else {}
        
        if self.customer_name_from_history:
            base_greeting_template = welcome_templates.get('with_customer_name', 
                "درودبرشما {customer_name} عزیز، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(
                    customer_name=self.customer_name_from_history,
                    service_name=service_name
                )
            except Exception:
                base_greeting = f"درودبرشما {self.customer_name_from_history} عزیز، با {service_name} تماس گرفته‌اید"
        else:
            base_greeting_template = welcome_templates.get('without_customer_name',
                "درودبرشما، با {service_name} تماس گرفته‌اید")
            try:
                base_greeting = base_greeting_template.format(service_name=service_name)
            except Exception:
                base_greeting = f"درودبرشما، با {service_name} تماس گرفته‌اید"
        
        if has_undelivered_order and orders:
            order_details = []
            for order in orders:
                status_display = order.get('status_display', '')
                items_text = self.format_items_list_persian(order.get('items', []))
                if items_text:
                    order_details.append(f"سفارش شما {items_text} {status_display} است")
                else:
                    order_details.append(f"سفارش شما {status_display} است")
            
            orders_text = "، ".join(order_details)
            closing = welcome_templates.get('closing_with_orders', " از صبر شما متشکریم.")
            return f"{base_greeting}، {orders_text}.{closing}"
        else:
            new_customer_msg = welcome_templates.get('new_customer_question', 
                " لطفا درخواست خود را بفرمایید.")
            return f"{base_greeting}.{new_customer_msg}"
    
    def format_items_list_persian(self, items):
        """Format items list in Persian (for restaurant orders)"""
        if not items:
            return ""
        
        persian_numbers = {
            1: "یک", 2: "دو", 3: "سه", 4: "چهار", 5: "پنج",
            6: "شش", 7: "هفت", 8: "هشت", 9: "نه", 10: "ده"
        }
        
        formatted_items = []
        for item in items:
            quantity = item.get('quantity', 1)
            item_name = (item.get('menu_item_name') or 
                        (item.get('menu_item', {}).get('name') if isinstance(item.get('menu_item'), dict) else None) or
                        item.get('name', ''))
            
            if not item_name:
                continue
            
            if quantity == 1:
                formatted_items.append(f"یک {item_name}")
            elif quantity <= 10:
                formatted_items.append(f"{persian_numbers.get(quantity, str(quantity))} {item_name}")
            else:
                formatted_items.append(f"{quantity} {item_name}")
        
        if not formatted_items:
            return ""
        elif len(formatted_items) == 1:
            return formatted_items[0]
        elif len(formatted_items) == 2:
            return f"{formatted_items[0]} و {formatted_items[1]}"
        else:
            all_except_last = "، ".join(formatted_items[:-1])
            return f"{all_except_last} و {formatted_items[-1]}"
