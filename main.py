import os
from contextlib import contextmanager
from pathlib import Path

import bcrypt
import pymysql
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).parent
CFG = dict(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", ""),
)
DB = os.getenv("MYSQL_DATABASE", "stockroom")

app = FastAPI(title="Stockroom")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
    same_site="lax",
    https_only=os.getenv("HTTPS_ONLY", "0") == "1",
)


@contextmanager
def cursor(database=DB):
    c = pymysql.connect(**CFG, database=database, cursorclass=pymysql.cursors.DictCursor,
                        charset="utf8mb4")
    try:
        with c.cursor() as k:
            yield k
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


@app.on_event("startup")
def setup():
    with cursor(database=None) as k:
        k.execute(f"CREATE DATABASE IF NOT EXISTS `{DB}` CHARACTER SET utf8mb4")
    with cursor() as k:
        for stmt in (BASE / "schema.sql").read_text().split(";"):
            if stmt.strip():
                k.execute(stmt)
        k.execute("SELECT COUNT(*) AS c FROM users")
        if k.fetchone()["c"] == 0:  # first run: demo data
            k.execute("INSERT INTO users(email,name,pass_hash) VALUES(%s,%s,%s)",
                      ("demo@example.com", "Demo User", hash_pw("demo1234")))
            k.executemany("INSERT INTO products(name,sku,price,stock) VALUES(%s,%s,%s,%s)", [
                ("Canvas tote", "TOTE-01", 18, 64), ("Ceramic mug", "MUG-02", 12.5, 8),
                ("Notebook A5", "NB-05", 7.25, 120), ("Desk lamp", "LMP-09", 46, 5),
                ("Steel bottle", "BTL-03", 24, 40)])
            k.executemany("INSERT INTO orders(product_id,qty,customer,status) VALUES(%s,%s,%s,%s)", [
                (1, 3, "Amara Silva", "shipped"), (2, 2, "Kasun Perera", "shipped"),
                (3, 10, "Nimal Fernando", "open"), (4, 1, "Dilini Jay", "open"),
                (5, 4, "Ravi Kumar", "shipped"), (1, 5, "Sara Ali", "shipped"),
                (3, 6, "Tom Reed", "cancelled")])


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Sign in to continue.")
    with cursor() as k:
        k.execute("SELECT id,name,email FROM users WHERE id=%s", (uid,))
        u = k.fetchone()
    if not u:
        request.session.clear()
        raise HTTPException(401, "Sign in to continue.")
    return u


# ---------- auth ----------
class Register(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=72)


class Login(BaseModel):
    email: str
    password: str


@app.post("/api/register")
def register(b: Register, request: Request):
    email = b.email.lower()
    with cursor() as k:
        k.execute("SELECT id FROM users WHERE email=%s", (email,))
        if k.fetchone():
            raise HTTPException(409, "That email already has an account. Sign in instead.")
        k.execute("INSERT INTO users(email,name,pass_hash) VALUES(%s,%s,%s)",
                  (email, b.name.strip(), hash_pw(b.password)))
        request.session["uid"] = k.lastrowid
    return {"ok": True}


@app.post("/api/login")
def login(b: Login, request: Request):
    with cursor() as k:
        k.execute("SELECT id,pass_hash FROM users WHERE email=%s", (b.email.strip().lower(),))
        u = k.fetchone()
    if not u or not bcrypt.checkpw(b.password.encode()[:72], u["pass_hash"].encode()):
        raise HTTPException(401, "Email or password is incorrect.")
    request.session["uid"] = u["id"]
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(current_user)):
    return u


# ---------- dashboard ----------
@app.get("/api/dashboard")
def dashboard(u=Depends(current_user)):
    with cursor() as k:
        k.execute("""SELECT COALESCE(SUM(o.qty*p.price),0) AS v FROM orders o
                     JOIN products p ON p.id=o.product_id WHERE o.status<>'cancelled'""")
        revenue = k.fetchone()["v"]
        k.execute("SELECT COUNT(*) AS c FROM orders WHERE status='open'")
        open_orders = k.fetchone()["c"]
        k.execute("SELECT COUNT(*) AS c FROM products WHERE stock<10")
        low = k.fetchone()["c"]
        k.execute("""SELECT p.name, SUM(o.qty*p.price) AS v FROM orders o JOIN products p
                     ON p.id=o.product_id WHERE o.status<>'cancelled' GROUP BY p.id, p.name
                     ORDER BY v DESC""")
        by = k.fetchall()
        k.execute("""SELECT o.id,o.customer,p.name AS product,o.qty,o.status FROM orders o
                     JOIN products p ON p.id=o.product_id ORDER BY o.id DESC LIMIT 5""")
        recent = k.fetchall()
    return dict(revenue=revenue, open_orders=open_orders, low_stock=low, by_product=by, recent=recent)


# ---------- products ----------
class Product(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    sku: str = Field(default="", max_length=60)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


@app.get("/api/products")
def products(u=Depends(current_user)):
    with cursor() as k:
        k.execute("SELECT * FROM products ORDER BY name")
        return k.fetchall()


@app.post("/api/products", status_code=201)
def add_product(p: Product, u=Depends(current_user)):
    with cursor() as k:
        k.execute("INSERT INTO products(name,sku,price,stock) VALUES(%s,%s,%s,%s)",
                  (p.name.strip(), p.sku.strip(), p.price, p.stock))
        return {"id": k.lastrowid}


@app.delete("/api/products/{pid}")
def delete_product(pid: int, u=Depends(current_user)):
    with cursor() as k:
        k.execute("SELECT id FROM orders WHERE product_id=%s LIMIT 1", (pid,))
        if k.fetchone():
            raise HTTPException(409, "This product has orders, so it can’t be deleted.")
        k.execute("DELETE FROM products WHERE id=%s", (pid,))
    return {"ok": True}


# ---------- orders ----------
class Order(BaseModel):
    product_id: int
    qty: int = Field(gt=0)
    customer: str = Field(min_length=1, max_length=160)


class Status(BaseModel):
    status: str = Field(pattern="^(open|shipped|cancelled)$")


@app.get("/api/orders")
def orders(u=Depends(current_user)):
    with cursor() as k:
        k.execute("""SELECT o.id,o.customer,p.name AS product,o.qty,o.status,o.created
                     FROM orders o JOIN products p ON p.id=o.product_id ORDER BY o.id DESC""")
        return k.fetchall()


@app.post("/api/orders", status_code=201)
def add_order(o: Order, u=Depends(current_user)):
    with cursor() as k:
        k.execute("SELECT stock FROM products WHERE id=%s FOR UPDATE", (o.product_id,))
        p = k.fetchone()
        if not p:
            raise HTTPException(404, "Product not found.")
        if o.qty > p["stock"]:
            raise HTTPException(409, f"Only {p['stock']} left in stock.")
        k.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (o.qty, o.product_id))
        k.execute("INSERT INTO orders(product_id,qty,customer) VALUES(%s,%s,%s)",
                  (o.product_id, o.qty, o.customer.strip()))
        return {"id": k.lastrowid}


@app.patch("/api/orders/{oid}")
def set_status(oid: int, s: Status, u=Depends(current_user)):
    with cursor() as k:
        k.execute("SELECT product_id,qty,status FROM orders WHERE id=%s FOR UPDATE", (oid,))
        o = k.fetchone()
        if not o:
            raise HTTPException(404, "Order not found.")
        if o["status"] != "cancelled" and s.status == "cancelled":
            k.execute("UPDATE products SET stock=stock+%s WHERE id=%s", (o["qty"], o["product_id"]))
        elif o["status"] == "cancelled" and s.status != "cancelled":
            k.execute("SELECT stock FROM products WHERE id=%s FOR UPDATE", (o["product_id"],))
            if k.fetchone()["stock"] < o["qty"]:
                raise HTTPException(409, "Not enough stock to reopen this order.")
            k.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (o["qty"], o["product_id"]))
        k.execute("UPDATE orders SET status=%s WHERE id=%s", (s.status, oid))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
