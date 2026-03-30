const state = {
    products: [],
    cart: [],
    selectedCardValue: 10
};

const $ = (id) => document.getElementById(id);

function formatMoney(n) {
    return Number(n || 0).toLocaleString("en-IN");
}

function escapeHtml(v) {
    return String(v || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

async function api(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        ...options
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.message || data.error || "Request failed");
    }
    return data;
}

async function loadWallet() {
    const data = await api("/api/store/wallet");
    $("gameBalance").textContent = formatMoney(data.game_balance || 0);
    $("storeBalance").textContent = formatMoney(data.store_balance || 0);
}

async function loadProducts() {
    $("productsGrid").innerHTML = `<div class="empty-card">Loading products...</div>`;
    const products = await api("/api/store/products");
    state.products = Array.isArray(products) ? products : [];

    if (!state.products.length) {
        $("productsGrid").innerHTML = `<div class="empty-card">No products available yet</div>`;
        return;
    }

    $("productsGrid").innerHTML = state.products.map((p) => {
        const lowStock = Number(p.stock || 0) <= 5;
        return `
            <div class="product-card">
                <div class="product-image">
                    ${p.image_url
                        ? `<img src="${escapeHtml(p.image_url)}" alt="${escapeHtml(p.title)}">`
                        : `<span>ShopperStyle Product</span>`}
                </div>
                <div class="product-body">
                    <h4 class="product-title">${escapeHtml(p.title)}</h4>
                    <p class="product-desc">${escapeHtml(p.description || "Premium store item")}</p>
                    <div class="product-meta">
                        <div class="product-price">₹${formatMoney(p.price)}</div>
                        <div class="stock-pill ${lowStock ? "low" : ""}">Stock: ${formatMoney(p.stock)}</div>
                    </div>
                    <button class="cart-btn" onclick="addToCart(${p.id})">Add to Cart</button>
                </div>
            </div>
        `;
    }).join("");
}

function addToCart(productId) {
    const product = state.products.find((p) => Number(p.id) === Number(productId));
    if (!product) return;

    const existing = state.cart.find((i) => Number(i.product_id) === Number(product.id));
    if (existing) {
        if (existing.qty < Number(product.stock || 0)) {
            existing.qty += 1;
        }
    } else {
        state.cart.push({
            product_id: product.id,
            title: product.title,
            price: Number(product.price || 0),
            qty: 1,
            stock: Number(product.stock || 0)
        });
    }
    renderCart();
}

function changeQty(productId, diff) {
    const item = state.cart.find((i) => Number(i.product_id) === Number(productId));
    if (!item) return;
    const nextQty = item.qty + diff;
    if (nextQty <= 0) {
        removeFromCart(productId);
        return;
    }
    if (nextQty > item.stock) return;
    item.qty = nextQty;
    renderCart();
}

function removeFromCart(productId) {
    state.cart = state.cart.filter((i) => Number(i.product_id) !== Number(productId));
    renderCart();
}

function cartTotal() {
    return state.cart.reduce((sum, item) => sum + item.price * item.qty, 0);
}

function renderCart() {
    const total = cartTotal();
    $("cartTotal").textContent = `₹${formatMoney(total)}`;

    if (!state.cart.length) {
        $("cartItems").innerHTML = `<div class="empty-card small">No items in cart</div>`;
        return;
    }

    $("cartItems").innerHTML = state.cart.map((item) => `
        <div class="cart-row">
            <div class="cart-row-top">
                <div>
                    <
