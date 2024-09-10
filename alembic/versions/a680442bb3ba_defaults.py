from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a680442bb3ba'
down_revision = '62233ec2615b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the 'in_stock' column with a default value of 'false'
    op.add_column('products', sa.Column('in_stock', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('products', sa.Column('last_seen_price', sa.Integer(), nullable=True))

    # Remove the default after the column has been added
    op.alter_column('products', 'in_stock', server_default=None)


def downgrade() -> None:
    # Drop the 'in_stock' and 'last_seen_price' columns
    op.drop_column('products', 'in_stock')
    op.drop_column('products', 'last_seen_price')
