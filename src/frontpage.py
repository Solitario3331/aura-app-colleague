import html

def build_page(brand: str, tagline: str, products: list[tuple[str, str]]) -> str:
    # Escape all user-provided text
    escaped_brand = html.escape(brand)
    escaped_tagline = html.escape(tagline)
    
    # Process products with escaping
    escaped_products = [(html.escape(name), html.escape(price)) for name, price in products]
    
    # Build the HTML content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escaped_brand}</title>
    <style>
        body {{
            background-color: white;
            color: black;
            font-family: sans-serif;
            margin: 0;
            padding: 20px;
            line-height: 1.6;
        }}
        header {{
            margin-bottom: 30px;
        }}
        nav {{
            margin-top: 10px;
        }}
        nav a {{
            margin-right: 15px;
            text-decoration: none;
            color: black;
        }}
        .hero {{
            margin-bottom: 30px;
            text-align: center;
        }}
        .button {{
            display: inline-block;
            padding: 10px 20px;
            background-color: #f0f0f0;
            text-decoration: none;
            color: black;
            border-radius: 4px;
            margin-top: 15px;
        }}
        #shop {{
            margin-bottom: 30px;
        }}
        h2 {{
            margin-bottom: 20px;
        }}
        .product {{
            margin-bottom: 20px;
        }}
        .img {{
            width: 100%;
            height: 200px;
            background-color: #ddd;
            margin-bottom: 10px;
        }}
        footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
        }}
        .products-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 20px;
        }}
        @media (max-width: 600px) {{
            .products-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>{escaped_brand}</h1>
        <nav>
            <a href="#shop">Shop</a>
            <a href="#about">About</a>
            <a href="#contact">Contact</a>
        </nav>
    </header>
    
    <section class="hero">
        <p>{escaped_tagline}</p>
        <a class="button" href="#shop">Shop now</a>
    </section>
    
    <section id="shop">
        <h2>Shop</h2>
        <div class="products-grid">
"""
    
    # Add products
    for name, price in escaped_products:
        html_content += f"""            <div class="product">
                <div class="img"></div>
                <h3>{name}</h3>
                <p>{price}</p>
            </div>
"""
    
    html_content += """        </div>
    </section>
    
    <footer id="contact">
        © """ + escaped_brand + """
    </footer>
</body>
</html>"""
    
    return html_content
