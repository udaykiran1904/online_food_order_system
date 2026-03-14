import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


@app.template_filter("format_inr")
def format_inr(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS food (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            food_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(food_id) REFERENCES food(id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            total_price REAL NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            food_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(food_id) REFERENCES food(id)
        )
        """
    )
    db.commit()


def normalize_food_images():
    db = get_db()
    rows = db.execute("SELECT id, image FROM food").fetchall()
    for row in rows:
        raw = row["image"] or ""
        normalized = raw.replace("\\", "/").lstrip("/")
        if normalized.startswith("static/"):
            normalized = normalized[len("static/") :]
        if normalized != raw:
            db.execute("UPDATE food SET image = ? WHERE id = ?", (normalized, row["id"]))
    db.commit()


def backfill_default_images():
    import os

    def pick_image(base_name: str):
        images_dir = os.path.join(app.root_path, "static", "images")
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
            filename = f"{base_name}{ext}"
            if os.path.exists(os.path.join(images_dir, filename)):
                return f"images/{filename}"
        return f"images/{base_name}.svg"

    db = get_db()
    rows = db.execute("SELECT id, name, image FROM food").fetchall()
    name_map = {
        "mutton curry": pick_image("mutton_curry"),
        "chicken fried rice": pick_image("chicken_fried_rice"),
        "briyani": pick_image("biryani"),
        "biryani": pick_image("biryani"),
        "chicken curry": pick_image("chicken_curry"),
        "paneer tikka": pick_image("paneer_tikka"),
        "masala dosa": pick_image("masala_dosa"),
    }
    for row in rows:
        if row["image"]:
            continue
        key = (row["name"] or "").strip().lower()
        image = name_map.get(key)
        if image:
            db.execute("UPDATE food SET image = ? WHERE id = ?", (image, row["id"]))
    db.commit()


def seed_default_foods():
    import os

    def pick_image(base_name: str):
        images_dir = os.path.join(app.root_path, "static", "images")
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
            filename = f"{base_name}{ext}"
            if os.path.exists(os.path.join(images_dir, filename)):
                return f"images/{filename}"
        return f"images/{base_name}.svg"

    db = get_db()
    row = db.execute("SELECT COUNT(*) AS count FROM food").fetchone()
    if row and row["count"] > 0:
        return
    defaults = [
        ("Mutton curry", 230.00, pick_image("mutton_curry")),
        ("Chicken fried rice", 120.00, pick_image("chicken_fried_rice")),
        ("Biryani", 150.00, pick_image("biryani")),
        ("Chicken curry", 150.00, pick_image("chicken_curry")),
        ("Paneer tikka", 190.00, pick_image("paneer_tikka")),
        ("Masala dosa", 90.00, pick_image("masala_dosa")),
    ]
    db.executemany("INSERT INTO food (name, price, image) VALUES (?, ?, ?)", defaults)
    db.commit()


@app.before_request
def ensure_db():
    init_db()
    normalize_food_images()
    backfill_default_images()
    seed_default_foods()


@app.context_processor
def inject_globals():
    cart_count = 0
    if session.get("user_id"):
        db = get_db()
        row = db.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS count FROM cart WHERE user_id = ?",
            (session["user_id"],),
        ).fetchone()
        cart_count = row["count"] if row else 0

    def resolve_image_url(path: str):
        if not path:
            return url_for("static", filename="images/placeholder.svg")
        if path.startswith("http://") or path.startswith("https://"):
            return path
        normalized = path.replace("\\", "/").lstrip("/")
        if normalized.startswith("static/"):
            normalized = normalized[len("static/") :]
        return url_for("static", filename=normalized)

    return {
        "cart_count": cart_count,
        "current_user": session.get("user_name"),
        "image_url": resolve_image_url,
    }


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_dashboard"))
        return view(*args, **kwargs)

    return wrapper


@app.route("/")
def home():
    return redirect(url_for("menu"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if len(password) < 6 or not any(ch.isdigit() for ch in password):
            flash("Password must be at least 6 characters and include a number.", "danger")
            return redirect(url_for("register"))

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, password),
            )
            db.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already registered.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email = ? AND password = ?",
            (email, password),
        ).fetchone()
        if user:
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["is_admin"] = False
            flash("Welcome back!", "success")
            return redirect(url_for("menu"))
        flash("Invalid login credentials.", "danger")
    return render_template("login.html", is_admin=False)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


@app.route("/menu")
def menu():
    search = request.args.get("search", "").strip()
    db = get_db()
    if search:
        foods = db.execute(
            "SELECT * FROM food WHERE name LIKE ? ORDER BY id DESC",
            (f"%{search}%",),
        ).fetchall()
    else:
        foods = db.execute("SELECT * FROM food ORDER BY id DESC").fetchall()
    return render_template("menu.html", foods=foods, search=search)


@app.route("/add_to_cart/<int:food_id>")
@login_required
def add_to_cart(food_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM cart WHERE user_id = ? AND food_id = ?",
        (session["user_id"], food_id),
    ).fetchone()
    if row:
        db.execute(
            "UPDATE cart SET quantity = quantity + 1 WHERE id = ?",
            (row["id"],),
        )
    else:
        db.execute(
            "INSERT INTO cart (user_id, food_id, quantity) VALUES (?, ?, 1)",
            (session["user_id"], food_id),
        )
    db.commit()
    flash("Added to cart.", "success")
    return redirect(url_for("menu"))


@app.route("/cart", methods=["GET", "POST"])
@login_required
def cart():
    db = get_db()
    if request.method == "POST":
        updates = request.form.getlist("quantity")
        ids = request.form.getlist("cart_id")
        for cart_id, qty in zip(ids, updates):
            qty_val = int(qty)
            if qty_val <= 0:
                db.execute("DELETE FROM cart WHERE id = ?", (cart_id,))
            else:
                db.execute("UPDATE cart SET quantity = ? WHERE id = ?", (qty_val, cart_id))
        db.commit()
        flash("Cart updated.", "success")
        return redirect(url_for("cart"))

    rows = db.execute(
        """
        SELECT cart.id AS cart_id, food.name, food.price, food.image, cart.quantity
        FROM cart
        JOIN food ON cart.food_id = food.id
        WHERE cart.user_id = ?
        """,
        (session["user_id"],),
    ).fetchall()
    total = sum(row["price"] * row["quantity"] for row in rows)
    return render_template("cart.html", cart_items=rows, total=total)


@app.route("/remove_cart/<int:cart_id>")
@login_required
def remove_cart(cart_id):
    db = get_db()
    db.execute(
        "DELETE FROM cart WHERE id = ? AND user_id = ?",
        (cart_id, session["user_id"]),
    )
    db.commit()
    flash("Item removed.", "info")
    return redirect(url_for("cart"))


@app.route("/checkout")
@login_required
def checkout():
    db = get_db()
    rows = db.execute(
        """
        SELECT cart.id AS cart_id, food.name, food.price, food.image, cart.quantity
        FROM cart
        JOIN food ON cart.food_id = food.id
        WHERE cart.user_id = ?
        """,
        (session["user_id"],),
    ).fetchall()
    total = sum(row["price"] * row["quantity"] for row in rows)
    return render_template("checkout.html", cart_items=rows, total=total)


@app.route("/place_order", methods=["POST"])
@login_required
def place_order():
    db = get_db()
    items = db.execute(
        """
        SELECT cart.food_id, cart.quantity, food.price
        FROM cart
        JOIN food ON cart.food_id = food.id
        WHERE cart.user_id = ?
        """,
        (session["user_id"],),
    ).fetchall()

    if not items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("cart"))

    total_price = sum(item["price"] * item["quantity"] for item in items)
    order_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    cursor = db.execute(
        "INSERT INTO orders (user_id, total_price, date) VALUES (?, ?, ?)",
        (session["user_id"], total_price, order_date),
    )
    order_id = cursor.lastrowid

    for item in items:
        db.execute(
            "INSERT INTO order_items (order_id, food_id, quantity) VALUES (?, ?, ?)",
            (order_id, item["food_id"], item["quantity"]),
        )

    db.execute("DELETE FROM cart WHERE user_id = ?", (session["user_id"],))
    db.commit()
    flash("Order placed successfully.", "success")
    return redirect(url_for("orders"))


@app.route("/orders")
@login_required
def orders():
    db = get_db()
    orders_rows = db.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC",
        (session["user_id"],),
    ).fetchall()

    order_items = {}
    for order in orders_rows:
        items = db.execute(
            """
            SELECT food.name, food.price, order_items.quantity
            FROM order_items
            JOIN food ON order_items.food_id = food.id
            WHERE order_items.order_id = ?
            """,
            (order["id"],),
        ).fetchall()
        order_items[order["id"]] = items

    return render_template("orders.html", orders=orders_rows, order_items=order_items, admin_view=False)


@app.route("/admin", methods=["GET", "POST"])
def admin_dashboard():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email == config.ADMIN_EMAIL and password == config.ADMIN_PASSWORD:
            session.clear()
            session["is_admin"] = True
            session["user_name"] = "Admin"
            flash("Admin login successful.", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "danger")
        return render_template("admin_dashboard.html", login_only=True)

    if not session.get("is_admin"):
        return render_template("admin_dashboard.html", login_only=True)

    db = get_db()
    foods = db.execute("SELECT * FROM food ORDER BY id DESC").fetchall()
    stats = db.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM food) AS food_count,
          (SELECT COUNT(*) FROM orders) AS order_count,
          (SELECT COUNT(*) FROM users) AS user_count
        """
    ).fetchone()
    return render_template("admin_dashboard.html", foods=foods, stats=stats, login_only=False)


@app.route("/admin/add_food", methods=["GET", "POST"])
@admin_required
def admin_add_food():
    import os
    from werkzeug.utils import secure_filename
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", type=float)
        image_url = request.form.get("image", "").strip()
        image_file = request.files.get("image_file")
        image_path = ""
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            images_dir = os.path.join(app.root_path, "static", "images")
            os.makedirs(images_dir, exist_ok=True)
            file_path = os.path.join(images_dir, filename)
            image_file.save(file_path)
            image_path = f"static/images/{filename}"
        elif image_url:
            image_path = image_url
        if name and price is not None:
            db = get_db()
            db.execute("INSERT INTO food (name, price, image) VALUES (?, ?, ?)", (name, price, image_path))
            db.commit()
            flash("Food item added.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin_add_food.html")


@app.route("/admin/edit_food/<int:food_id>", methods=["GET", "POST"])
@admin_required
def admin_edit_food(food_id):
    db = get_db()
    food = db.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
    if not food:
        flash("Food item not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    import os
    from werkzeug.utils import secure_filename
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", type=float)
        image_url = request.form.get("image", "").strip()
        image_file = request.files.get("image_file")
        image_path = food["image"]
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            images_dir = os.path.join(app.root_path, "static", "images")
            os.makedirs(images_dir, exist_ok=True)
            file_path = os.path.join(images_dir, filename)
            image_file.save(file_path)
            image_path = f"static/images/{filename}"
        elif image_url:
            image_path = image_url
        if name and price is not None:
            db.execute(
                "UPDATE food SET name = ?, price = ?, image = ? WHERE id = ?",
                (name, price, image_path, food_id),
            )
            db.commit()
            flash("Food item updated.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template("admin_edit_food.html", food=food)


@app.route("/admin/delete_food/<int:food_id>")
@admin_required
def admin_delete_food(food_id):
    db = get_db()
    db.execute("DELETE FROM food WHERE id = ?", (food_id,))
    db.commit()
    flash("Food item deleted.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/orders")
@admin_required
def admin_orders():
    db = get_db()
    rows = db.execute(
        """
        SELECT orders.id, orders.total_price, orders.date, users.name
        FROM orders
        JOIN users ON orders.user_id = users.id
        ORDER BY orders.id DESC
        """
    ).fetchall()

    order_items = {}
    for order in rows:
        items = db.execute(
            """
            SELECT food.name, food.price, order_items.quantity
            FROM order_items
            JOIN food ON order_items.food_id = food.id
            WHERE order_items.order_id = ?
            """,
            (order["id"],),
        ).fetchall()
        order_items[order["id"]] = items

    return render_template("admin_orders.html", orders=rows, order_items=order_items)


if __name__ == "__main__":
    app.run(debug=True)
