from sqlalchemy import Column, Integer, String, ForeignKey, ARRAY, Index, DateTime, func
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()

# Product model
class Product(Base):
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    image_urls = Column(ARRAY(String))  # Store image URLs as an array of strings
    product_url = Column(String, unique=True, nullable=False, index=True)  # Index on product_url
    source = Column(String, nullable=False)  # Source, e.g., 'walgreens'

    # Timestamps
    created_date = Column(DateTime, default=func.now(), nullable=False)
    updated_date = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationship with ProductMatch
    product_matches = relationship('ProductMatch', back_populates='product')


# AmazonProduct model
class AmazonProduct(Base):
    __tablename__ = 'amazon_products'

    id = Column(Integer, primary_key=True, autoincrement=True)
    asin = Column(String, unique=True, nullable=False, index=True)  # Index on asin
    title = Column(String, nullable=False)
    product_url = Column(String, unique=True, nullable=False)
    image_url = Column(String, nullable=False)

    # Timestamps
    created_date = Column(DateTime, default=func.now(), nullable=False)
    updated_date = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


# ProductMatch model (many-to-many relation between Product and AmazonProduct)
class ProductMatch(Base):
    __tablename__ = 'product_matches'

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey('products.id', ondelete="CASCADE"), nullable=False)
    amazon_product_id = Column(Integer, ForeignKey('amazon_products.id', ondelete="CASCADE"), nullable=False)

    # Composite index on product_id and amazon_product_id for faster lookups
    __table_args__ = (
        Index('ix_product_amazon_match', 'product_id', 'amazon_product_id', unique=True),
    )

    # Timestamps
    created_date = Column(DateTime, default=func.now(), nullable=False)
    updated_date = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    product = relationship('Product', back_populates='product_matches')
    amazon_product = relationship('AmazonProduct')
