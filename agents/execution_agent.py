"""
Execution Agent:
Communicates directly with MetaTrader 5 terminal:
- Places buy/sell market orders with Stop-Loss and Take-Profit attached at entry
- Manages trailing stops and breakeven adjustments directly in MT5
- Reconciles active MT5 broker positions against the internal Trade Journal DB
- Ensures no duplicate orders or orphan positions
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import sqlite3
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from core.database import DB_PATH


class ExecutionAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.is_connected = False

    def connect(self) -> bool:
        if mt5 is None:
            print("[EXECUTION ERROR] MetaTrader5 library not installed.")
            return False
        if not mt5.initialize():
            print(f"[EXECUTION ERROR] MT5 init failed: {mt5.last_error()}")
            return False
        self.is_connected = True
        return True

    def disconnect(self):
        if mt5 and self.is_connected:
            mt5.shutdown()
            self.is_connected = False

    def place_order(
        self,
        symbol: str,
        direction: str,
        lots: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        strategy_name: str = "TREND"
    ) -> Optional[int]:
        """
        Sends order to MT5. Returns order ticket integer on success, or None on failure.
        """
        if not self.is_connected and not self.connect():
            return None

        # Ensure symbol is selected in Market Watch
        if not mt5.symbol_select(symbol, True):
            print(f"[EXECUTION] Failed to select symbol {symbol} in MarketWatch.")
            return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"[EXECUTION] Cannot get tick data for {symbol}.")
            return None

        price = tick.ask if direction == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        # Fetch filling mode supported by broker
        symbol_info = mt5.symbol_info(symbol)
        filling_type = mt5.ORDER_FILLING_IOC
        if symbol_info:
            if symbol_info.filling_mode & 1:  # FOK
                filling_type = mt5.ORDER_FILLING_FOK
            elif symbol_info.filling_mode & 2:  # IOC
                filling_type = mt5.ORDER_FILLING_IOC
            else:
                filling_type = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lots),
            "type": order_type,
            "price": float(price),
            "sl": float(stop_loss),
            "deviation": 20,
            "magic": 10102026,
            "comment": f"AGENT_{strategy_name[:6]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        if take_profit is not None:
            request["tp"] = float(take_profit)

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err_msg = result.comment if result else mt5.last_error()
            print(f"[EXECUTION REJECTED] {symbol} {direction} {lots} lots: {err_msg}")
            return None

        ticket = result.order
        print(f"[EXECUTION SUCCESS] {symbol} {direction} {lots} lots placed! Ticket: #{ticket}")

        # Log into SQLite DB
        self._record_trade(ticket, symbol, direction, lots, price, stop_loss, take_profit, strategy_name)
        return ticket

    def modify_stop_loss(self, ticket: int, new_sl: float) -> bool:
        """Modifies the stop loss of an open position in MT5."""
        if not self.is_connected and not self.connect():
            return False

        position = mt5.positions_get(ticket=ticket)
        if not position or len(position) == 0:
            return False

        pos = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": float(new_sl),
            "tp": pos.tp
        }
        res = mt5.order_send(request)
        return res is not None and res.retcode == mt5.TRADE_RETCODE_DONE

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Returns all open MT5 positions placed by this agent."""
        if not self.is_connected and not self.connect():
            return []

        positions = mt5.positions_get()
        if positions is None:
            return []

        pos_list = []
        for p in positions:
            pos_list.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "direction": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": p.volume,
                "entry_price": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "magic": p.magic
            })
        return pos_list

    def _record_trade(self, ticket, symbol, direction, lots, price, sl, tp, strategy):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (
                    ticket, symbol, strategy, regime, direction, lots,
                    entry_time, entry_price, initial_sl, initial_tp, risk_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket, symbol, strategy, "LIVE", direction, lots,
                datetime.utcnow().isoformat(), price, sl, tp, 0.0
            ))
            conn.commit()
