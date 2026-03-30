from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import wraps
import os
import hashlib
import secrets
import re
import time

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-this-in-production")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

database_url = os.environ.get("DATABASE_URL", "").strip()
if database_url:
    if database_url.startswith("mysql://"):
        database_url = database_url.replace("mysql://", "mysql+pymysql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    db_path = os.path.join(os.path.dirname(__file__), "store.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

db = SQLAlchemy(app)

IST = ZoneInfo("Asia/Kolkata")
ALLOWED_CARD_VALUES = {10, 50, 100, 200}
DEFAULT_GAME_WALLET_BALANCE = int(os.environ.get("DEFAULT_GAME_WALLET_BALANCE", "10000"))


# ---------------------------------------------------
# Time helpers
# ---------------------------------------------------

def as_utc(dt: datetime) -> datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_ist(dt: datetime) -> datetime:
    if dt is None:
        return None
    return as_utc(dt).astimezone(IST)


def fmt_ist(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    d = as_ist(dt)
    return d.strftime(fmt) if d else ""


def _safe_int(v, default=0):
    try:
        return int(v or 0)
    except Exception:
        return default


def is_valid_username(username: str) -> bool:
    username = (username or "").strip()
    if not username:
        return False
    if " " in username:
        return False
    return re.fullmatch(r"[A-Za-z0-9_]+", username) is not None


def _get_session_user_id():
    return session.get("user_id") or session.get("userid") or session.get("userId")


def get_current_logged_in_user():
    uid = _get_session_user_id()
    if not uid:
        return None
    try:
        if str(uid).isdigit():
            return User.query.get(int(uid))
        return User.query.get(uid)
    except Exception:
        return None


def make_order_code(user_id):
    stamp = as_ist(datetime.utcnow()).strftime("%Y%m%d%H%M%S")
    return f"ORD{stamp}{int(user_id)}{secrets.token_hex(2).upper()}"


def set_user_session(user):
    session["user_id"] = user.id
    session["userid"] = user.id
    session["userId"] = user.id
    session["username"] = user.username
    session.permanent = True


# ---------------------------------------------------
# Decorators
# ---------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = _get_session_user_id()
        if not uid:
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="Login required"), 401
            return redirect(url_for("login_page"))

        user = User.query.get(int(uid)) if str(uid).isdigit() else User.query.get(uid)
        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="User not found"), 401
            return redirect(url_for("login_page"))

        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = _get_session_user_id()
        if not uid:
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="Admin login required"), 401
            return redirect(url_for("login_page"))

        user = User.query.get(int(uid)) if str(uid).isdigit() else User.query.get(uid)
        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="User not found"), 401
            return redirect(url_for("login_page"))

        if not bool(getattr(user, "is_admin", False)):
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="Admin access required"), 403
            return redirect(url_for("store_home"))

        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------
# Models
# Must match your existing shared DB structure
# ---------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    display_name = db.Column(db.String(120))
    email = db.Column(db.String(200))
    country = db.Column(db.String(100))
    phone = db.Column(db.String(50))

    is_admin = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    block_reason = db.Column(db.Text)

    agentid = db.Column(db.Integer, nullable=True, index=True)

    def set_password(self, password: str):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def check_password(self, password: str) -> bool:
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()


class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    balance = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(100))
    game_title = db.Column(db.String(100))
    note = db.Column(db.Text)
    datetime = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class StoreWallet(db.Model):
    __tablename__ = "store_wallet"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    balance = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StoreTransaction(db.Model):
    __tablename__ = "store_transaction"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(50), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(120))
    note = db.Column(db.Text)
    reference = db.Column(db.String(120), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class Product(db.Model):
    __tablename__ = "product"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class UserAddress(db.Model):
    __tablename__ = "user_address"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    line1 = db.Column(db.String(250), nullable=False)
    line2 = db.Column(db.String(250))
    city = db.Column(db.String(120), nullable=False)
    state = db.Column(db.String(120), nullable=False)
    pincode = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(100), default="India", nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StoreOrder(db.Model):
    __tablename__ = "store_order"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    address_id = db.Column(db.Integer, db.ForeignKey("user_address.id"), nullable=True, index=True)
    order_code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    subtotal = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(30), default="PLACED", nullable=False, index=True)
    payment_mode = db.Column(db.String(30), default="STORE_WALLET", nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class StoreOrderItem(db.Model):
    __tablename__ = "store_order_item"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("store_order.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    product_title = db.Column(db.String(200), nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)
    qty = db.Column(db.Integer, nullable=False, default=1)
    line_total = db.Column(db.Integer, nullable=False)


class PointCardPurchase(db.Model):
    __tablename__ = "point_card_purchase"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    card_value = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    total_coins = db.Column(db.Integer, nullable=False)
    payment_status = db.Column(db.String(30), default="PAID", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class WalletTransfer(db.Model):
    __tablename__ = "wallet_transfer"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    direction = db.Column(db.String(30), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="SUCCESS", nullable=False, index=True)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


# ---------------------------------------------------
# Wallet helpers
# ---------------------------------------------------

def ensure_wallet_for_user(user, starting_balance=DEFAULT_GAME_WALLET_BALANCE):
    if not user:
        return None
    if getattr(user, "is_admin", False):
        return None

    wallet = Wallet.query.filter_by(user_id=user.id).first()
    if not wallet:
        wallet = Wallet(user_id=user.id, balance=int(starting_balance or 0))
        db.session.add(wallet)
        db.session.commit()
    return wallet


def ensure_store_wallet_for_user(user, starting_balance=0):
    if not user:
        return None
    if getattr(user, "is_admin", False):
        return None

    wallet = StoreWallet.query.filter_by(user_id=user.id).first()
    if not wallet:
        wallet = StoreWallet(user_id=user.id, balance=int(starting_balance or 0))
        db.session.add(wallet)
        db.session.commit()
    return wallet


# ---------------------------------------------------
# Page routes
# ---------------------------------------------------

@app.route("/")
def index():
    user = get_current_logged_in_user()
    if not user:
        return redirect(url_for("login_page"))
    if getattr(user, "is_admin", False):
        return redirect(url_for("admin_store_page"))
    return redirect(url_for("store_home"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        user = get_current_logged_in_user()
        if user:
            if getattr(user, "is_admin", False):
                return redirect(url_for("admin_store_page"))
            return redirect(url_for("store_home"))
        return render_template("store-login.html", error="")

    data = request.get_json(silent=True) or {}

    username = (request.form.get("username") or data.get("username") or "").strip()
    password = (request.form.get("password") or data.get("password") or "").strip()

    if not username or not password:
        if request.is_json:
            return jsonify(success=False, message="Username and password required"), 400
        return render_template("store-login.html", error="Username and password required")

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        if request.is_json:
            return jsonify(success=False, message="Invalid username or password"), 401
        return render_template("store-login.html", error="Invalid username or password")

    if getattr(user, "is_blocked", False):
        msg = f"Your account is blocked. Reason: {user.block_reason or 'No reason provided'}"
        if request.is_json:
            return jsonify(success=False, message=msg), 403
        return render_template("store-login.html", error=msg)

    ensure_wallet_for_user(user)
    ensure_store_wallet_for_user(user)
    set_user_session(user)

    redirect_to = url_for("admin_store_page") if getattr(user, "is_admin", False) else url_for("store_home")
    if request.is_json:
        return jsonify(success=True, redirect=redirect_to)
    return redirect(redirect_to)

@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        user = get_current_logged_in_user()
        if user:
            return redirect(url_for("store_home"))
        return render_template("store-register.html", error="")

    data = request.get_json(silent=True) or {}

    username = (request.form.get("username") or data.get("username") or "").strip()
    password = (request.form.get("password") or data.get("password") or "").strip()

    if not username or not password:
        if request.is_json:
            return jsonify(success=False, message="Username and password required"), 400
        return render_template("store-register.html", error="Username and password required")

    if not is_valid_username(username):
        msg = "Username can use only letters, numbers, and underscore"
        if request.is_json:
            return jsonify(success=False, message=msg), 400
        return render_template("store-register.html", error=msg)

    if len(password) < 6:
        msg = "Password must be at least 6 characters"
        if request.is_json:
            return jsonify(success=False, message=msg), 400
        return render_template("store-register.html", error=msg)

    existing = User.query.filter_by(username=username).first()
    if existing:
        msg = "Username already exists"
        if request.is_json:
            return jsonify(success=False, message=msg), 400
        return render_template("store-register.html", error=msg)

    user = User(username=username, display_name=username, country="India")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    ensure_wallet_for_user(user)
    ensure_store_wallet_for_user(user)
    set_user_session(user)

    if request.is_json:
        return jsonify(success=True, redirect=url_for("store_home"))
    return redirect(url_for("store_home"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/store")
@login_required
def store_home():
    return render_template("store-home.html", username=session.get("username", "User"))


@app.route("/admin-store")
@admin_required
def admin_store_page():
    return render_template("admin-store.html", username=session.get("username", "Admin"))


# ---------------------------------------------------
# Store APIs
# ---------------------------------------------------

@app.route("/api/me")
@login_required
def api_me():
    user = get_current_logged_in_user()
    return jsonify(
        success=True,
        user={
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name or user.username,
            "is_admin": bool(getattr(user, "is_admin", False)),
        }
    )


@app.route("/api/store/wallet", methods=["GET"])
@login_required
def api_store_wallet():
    user = get_current_logged_in_user()
    game_wallet = ensure_wallet_for_user(user)
    store_wallet = ensure_store_wallet_for_user(user)

    return jsonify(
        success=True,
        game_balance=int(game_wallet.balance or 0) if game_wallet else 0,
        store_balance=int(store_wallet.balance or 0) if store_wallet else 0,
    )


@app.route("/api/store/products", methods=["GET"])
@login_required
def api_store_products():
    products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).all()
    return jsonify([
        {
            "id": p.id,
            "title": p.title,
            "slug": p.slug,
            "description": p.description or "",
            "price": int(p.price or 0),
            "stock": int(p.stock or 0),
            "image_url": p.image_url or "",
            "is_active": bool(p.is_active),
        }
        for p in products
    ])


@app.route("/api/store/buy-card", methods=["POST"])
@login_required
def api_store_buy_card():
    user = get_current_logged_in_user()
    data = request.get_json(silent=True) or {}

    card_value = _safe_int(data.get("card_value") or data.get("cardvalue"), 0)
    quantity = _safe_int(data.get("quantity"), 1)

    if card_value not in ALLOWED_CARD_VALUES:
        return jsonify(success=False, message="Invalid card value"), 400
    if quantity <= 0:
        return jsonify(success=False, message="Invalid quantity"), 400

    total_coins = card_value * quantity
    reference = f"CARD{int(time.time())}{user.id}"

    try:
        game_wallet = ensure_wallet_for_user(user)
        ensure_store_wallet_for_user(user)

        game_wallet.balance = int(game_wallet.balance or 0) + total_coins

        db.session.add(Transaction(
            user_id=user.id,
            kind="added",
            amount=total_coins,
            balance_after=int(game_wallet.balance or 0),
            label="Point Card Added",
            game_title="Store Card",
            note=f"Bought {quantity} card(s) of {card_value}"
        ))

        db.session.add(PointCardPurchase(
            user_id=user.id,
            card_value=card_value,
            quantity=quantity,
            total_coins=total_coins,
            payment_status="PAID"
        ))

        db.session.add(WalletTransfer(
            user_id=user.id,
            direction="STORE_TO_GAME",
            amount=total_coins,
            status="SUCCESS",
            note=f"Card purchase reference {reference}"
        ))

        db.session.commit()

        return jsonify(
            success=True,
            message="Coins added successfully",
            added=total_coins,
            game_balance=int(game_wallet.balance or 0),
            reference=reference
        )
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"Buy card failed: {str(e)}"), 500


@app.route("/api/store/redeem-from-game", methods=["POST"])
@login_required
def api_store_redeem_from_game():
    user = get_current_logged_in_user()
    data = request.get_json(silent=True) or {}
    amount = _safe_int(data.get("amount"), 0)

    if amount <= 0:
        return jsonify(success=False, message="Enter valid amount"), 400

    try:
        game_wallet = ensure_wallet_for_user(user)
        store_wallet = ensure_store_wallet_for_user(user)

        if int(game_wallet.balance or 0) < amount:
            return jsonify(success=False, message="Insufficient game balance"), 400

        game_wallet.balance = int(game_wallet.balance or 0) - amount
        store_wallet.balance = int(store_wallet.balance or 0) + amount

        db.session.add(Transaction(
            user_id=user.id,
            kind="redeem",
            amount=amount,
            balance_after=int(game_wallet.balance or 0),
            label="Moved To Store Wallet",
            game_title="Store Transfer",
            note=f"Game to store transfer of {amount}"
        ))

        db.session.add(StoreTransaction(
            user_id=user.id,
            kind="game_to_store",
            amount=amount,
            balance_after=int(store_wallet.balance or 0),
            label="Received From Game Wallet",
            note=f"Game to store transfer of {amount}"
        ))

        db.session.add(WalletTransfer(
            user_id=user.id,
            direction="GAME_TO_STORE",
            amount=amount,
            status="SUCCESS",
            note="Redeemed back to store wallet"
        ))

        db.session.commit()

        return jsonify(
            success=True,
            message="Amount moved to store wallet",
            game_balance=int(game_wallet.balance or 0),
            store_balance=int(store_wallet.balance or 0)
        )
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"Redeem failed: {str(e)}"), 500


@app.route("/api/store/addresses", methods=["GET", "POST"])
@login_required
def api_store_addresses():
    user = get_current_logged_in_user()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}

        full_name = (data.get("full_name") or data.get("fullname") or "").strip()
        phone = (data.get("phone") or "").strip()
        line1 = (data.get("line1") or "").strip()
        line2 = (data.get("line2") or "").strip()
        city = (data.get("city") or "").strip()
        state = (data.get("state") or "").strip()
        pincode = (data.get("pincode") or "").strip()
        country = (data.get("country") or "India").strip()
        is_default = bool(data.get("is_default") or data.get("isdefault"))

        if not full_name or not phone or not line1 or not city or not state or not pincode:
            return jsonify(success=False, message="Missing address fields"), 400

        try:
            if is_default:
                UserAddress.query.filter_by(user_id=user.id, is_default=True).update({"is_default": False})

            address = UserAddress(
                user_id=user.id,
                full_name=full_name,
                phone=phone,
                line1=line1,
                line2=line2 or None,
                city=city,
                state=state,
                pincode=pincode,
                country=country,
                is_default=is_default
            )
            db.session.add(address)
            db.session.commit()
            return jsonify(success=True, message="Address saved", address_id=address.id)
        except Exception as e:
            db.session.rollback()
            return jsonify(success=False, message=f"Address save failed: {str(e)}"), 500

    addresses = UserAddress.query.filter_by(user_id=user.id).order_by(UserAddress.created_at.desc()).all()
    return jsonify([
        {
            "id": a.id,
            "full_name": a.full_name,
            "phone": a.phone,
            "line1": a.line1,
            "line2": a.line2 or "",
            "city": a.city,
            "state": a.state,
            "pincode": a.pincode,
            "country": a.country,
            "is_default": bool(a.is_default),
        }
        for a in addresses
    ])


@app.route("/api/store/checkout", methods=["POST"])
@login_required
def api_store_checkout():
    user = get_current_logged_in_user()
    data = request.get_json(silent=True) or {}

    items = data.get("items") or []
    address_id = data.get("address_id") or data.get("addressid")
    note = (data.get("note") or "").strip()

    if not isinstance(items, list) or not items:
        return jsonify(success=False, message="Cart is empty"), 400

    try:
        store_wallet = ensure_store_wallet_for_user(user)
        address = None

        if address_id not in (None, "", 0, "0"):
            address = UserAddress.query.filter_by(
                id=_safe_int(address_id, 0),
                user_id=user.id
            ).first()
            if not address:
                return jsonify(success=False, message="Address not found"), 404

        subtotal = 0
        product_rows = []

        for item in items:
            product_id = _safe_int(item.get("product_id") or item.get("productid"), 0)
            qty = _safe_int(item.get("qty") or item.get("quantity"), 1)

            if product_id <= 0 or qty <= 0:
                return jsonify(success=False, message="Invalid cart item"), 400

            product = Product.query.get(product_id)
            if not product or not product.is_active:
                return jsonify(success=False, message="Product not available"), 404

            if int(product.stock or 0) < qty:
                return jsonify(success=False, message=f"Insufficient stock for {product.title}"), 400

            line_total = int(product.price or 0) * qty
            subtotal += line_total
            product_rows.append((product, qty, line_total))

        total = subtotal

        if int(store_wallet.balance or 0) < total:
            return jsonify(success=False, message="Insufficient store wallet balance"), 400

        store_wallet.balance = int(store_wallet.balance or 0) - total

        order = StoreOrder(
            user_id=user.id,
            address_id=address.id if address else None,
            order_code=make_order_code(user.id),
            subtotal=subtotal,
            total=total,
            status="PLACED",
            payment_mode="STORE_WALLET",
            note=note or None
        )
        db.session.add(order)
        db.session.flush()

        for product, qty, line_total in product_rows:
            product.stock = int(product.stock or 0) - qty
            db.session.add(StoreOrderItem(
                order_id=order.id,
                product_id=product.id,
                product_title=product.title,
                unit_price=int(product.price or 0),
                qty=qty,
                line_total=line_total
            ))

        db.session.add(StoreTransaction(
            user_id=user.id,
            kind="product_purchase",
            amount=total,
            balance_after=int(store_wallet.balance or 0),
            label="Order placed",
            note=f"Order {order.order_code}",
            reference=order.order_code
        ))

        db.session.commit()

        return jsonify(
            success=True,
            message="Order placed successfully",
            order_code=order.order_code,
            total=total,
            store_balance=int(store_wallet.balance or 0)
        )
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"Checkout failed: {str(e)}"), 500


@app.route("/api/store/orders", methods=["GET"])
@login_required
def api_store_orders():
    user = get_current_logged_in_user()
    orders = StoreOrder.query.filter_by(user_id=user.id).order_by(StoreOrder.created_at.desc()).all()

    out = []
    for order in orders:
        items = StoreOrderItem.query.filter_by(order_id=order.id).all()
        out.append({
            "id": order.id,
            "order_code": order.order_code,
            "status": order.status,
            "subtotal": int(order.subtotal or 0),
            "total": int(order.total or 0),
            "payment_mode": order.payment_mode,
            "created_at": fmt_ist(order.created_at, "%Y-%m-%d %H:%M"),
            "items": [
                {
                    "product_title": item.product_title,
                    "unit_price": int(item.unit_price or 0),
                    "qty": int(item.qty or 0),
                    "line_total": int(item.line_total or 0),
                }
                for item in items
            ]
        })
    return jsonify(out)


@app.route("/api/store/history", methods=["GET"])
@login_required
def api_store_history():
    user = get_current_logged_in_user()
    rows = StoreTransaction.query.filter_by(user_id=user.id).order_by(StoreTransaction.created_at.desc()).limit(50).all()

    return jsonify([
        {
            "kind": r.kind,
            "amount": int(r.amount or 0),
            "balance_after": int(r.balance_after or 0),
            "label": r.label or "",
            "note": r.note or "",
            "reference": r.reference or "",
            "created_at": fmt_ist(r.created_at, "%Y-%m-%d %H:%M"),
        }
        for r in rows
    ])


# ---------------------------------------------------
# Admin APIs
# ---------------------------------------------------

@app.route("/api/admin/store/products", methods=["GET", "POST"])
@admin_required
def api_admin_store_products():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        slug = (data.get("slug") or "").strip().lower()
        description = (data.get("description") or "").strip()
        price = _safe_int(data.get("price"), 0)
        stock = _safe_int(data.get("stock"), 0)
        image_url = (data.get("image_url") or data.get("imageurl") or "").strip()

        if not title or not slug:
            return jsonify(success=False, message="Title and slug are required"), 400
        if price <= 0:
            return jsonify(success=False, message="Price must be greater than 0"), 400
        if stock < 0:
            return jsonify(success=False, message="Stock cannot be negative"), 400
        if Product.query.filter_by(slug=slug).first():
            return jsonify(success=False, message="Slug already exists"), 400

        try:
            product = Product(
                title=title,
                slug=slug,
                description=description or None,
                price=price,
                stock=stock,
                image_url=image_url or None,
                is_active=True
            )
            db.session.add(product)
            db.session.commit()
            return jsonify(success=True, message="Product created", product_id=product.id)
        except Exception as e:
            db.session.rollback()
            return jsonify(success=False, message=f"Product create failed: {str(e)}"), 500

    products = Product.query.order_by(Product.created_at.desc()).all()
    return jsonify([
        {
            "id": p.id,
            "title": p.title,
            "slug": p.slug,
            "description": p.description or "",
            "price": int(p.price or 0),
            "stock": int(p.stock or 0),
            "image_url": p.image_url or "",
            "is_active": bool(p.is_active),
            "created_at": fmt_ist(p.created_at, "%Y-%m-%d %H:%M") if p.created_at else ""
        }
        for p in products
    ])


@app.route("/api/admin/store/products/<int:product_id>", methods=["POST"])
@admin_required
def api_admin_store_product_update(product_id):
    product = Product.query.get(product_id)
    if not product:
        return jsonify(success=False, message="Product not found"), 404

    data = request.get_json(silent=True) or {}

    try:
        if "title" in data:
            product.title = (data.get("title") or "").strip() or product.title
        if "description" in data:
            product.description = (data.get("description") or "").strip() or None
        if "price" in data:
            price = _safe_int(data.get("price"), product.price)
            if price <= 0:
                return jsonify(success=False, message="Price must be greater than 0"), 400
            product.price = price
        if "stock" in data:
            stock = _safe_int(data.get("stock"), product.stock)
            if stock < 0:
                return jsonify(success=False, message="Stock cannot be negative"), 400
            product.stock = stock
        if "image_url" in data:
            product.image_url = (data.get("image_url") or "").strip() or None
        if "is_active" in data:
            product.is_active = bool(data.get("is_active"))

        db.session.commit()
        return jsonify(success=True, message="Product updated")
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"Product update failed: {str(e)}"), 500


@app.route("/api/admin/store/orders", methods=["GET"])
@admin_required
def api_admin_store_orders():
    orders = StoreOrder.query.order_by(StoreOrder.created_at.desc()).limit(200).all()
    out = []
    for order in orders:
        user = User.query.get(order.user_id)
        out.append({
            "id": order.id,
            "order_code": order.order_code,
            "username": user.username if user else f"user-{order.user_id}",
            "status": order.status,
            "total": int(order.total or 0),
            "created_at": fmt_ist(order.created_at, "%Y-%m-%d %H:%M"),
        })
    return jsonify(out)


@app.route("/api/admin/store/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def api_admin_store_order_status(order_id):
    order = StoreOrder.query.get(order_id)
    if not order:
        return jsonify(success=False, message="Order not found"), 404

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().upper()

    allowed = {"PLACED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"}
    if status not in allowed:
        return jsonify(success=False, message="Invalid status"), 400

    try:
        order.status = status
        db.session.commit()
        return jsonify(success=True, message="Order status updated")
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"Status update failed: {str(e)}"), 500


# ---------------------------------------------------
# Startup
# ---------------------------------------------------

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
