"""Ruby on Rails framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Rails 7+ API-only + ActiveRecord + ActiveModel Serializers + RSpec backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

RAILS_RULES: tuple[str, ...] = (
    "1. Use ActiveRecord models — NEVER use raw SQL or alternative ORMs. "
    "Inherit from `ApplicationRecord`. Use `has_many`, `belongs_to`, `has_one` "
    "for associations and built-in validations (`validates`, `validate`)",
    "2. Use ActiveModel Serializers for JSON responses — NEVER manually build "
    "JSON hashes or use `as_json`. Define `attributes` and `has_many`/`belongs_to` "
    "in serializer classes inheriting from `ActiveModel::Serializer`",
    "3. Use API-namespaced controllers under `Api::V1::` — inherit from "
    "`ApplicationController`. NEVER use scaffold-generated HTML controllers "
    "or respond_to blocks with HTML formats",
    "4. Use strong parameters — NEVER use `params.permit!` or mass-assign "
    "unfiltered params. Define private `*_params` methods that call "
    "`params.require(:resource).permit(:field1, :field2)`",
    "5. Use concerns for shared model and controller logic — extract common "
    "behavior into `app/models/concerns/` and `app/controllers/concerns/`. "
    "Use `extend ActiveSupport::Concern` with `included` blocks",
    "6. Use ActiveRecord migrations for all schema changes — NEVER modify "
    "`schema.rb` directly. Use `rails generate migration` style with "
    "`change`, `up`/`down` methods, and proper index definitions",
    "7. Use `before_action` callbacks for authentication and authorization — "
    "define `authenticate_user!` in `ApplicationController` and skip "
    "selectively with `skip_before_action`",
    "8. Use scopes and class methods on models for query logic — "
    "NEVER put complex queries in controllers. Define `scope :name, -> { ... }` "
    "or `def self.method_name` on the model",
    "9. Use `render json:` with serializers and proper HTTP status codes — "
    "use symbol status names (`:ok`, `:created`, `:unprocessable_entity`, `:not_found`). "
    "NEVER use numeric codes directly",
    "10. Every model MUST define validations, associations, and scopes — "
    "add `created_at` and `updated_at` timestamps via `t.timestamps` in migrations. "
    "Use `db/migrate/` for all schema changes",
    "11. NEVER use 'raise NotImplementedError', '# TODO', or empty method bodies — "
    "every method must have a REAL, COMPLETE implementation",
    "12. NEVER invent require/include paths — use ONLY names from the contract "
    "and previously generated code",
    "13. Use `bcrypt` gem with `has_secure_password` for authentication — "
    "use `jwt` gem for token generation and verification with HS256 algorithm "
    "and expiration claims",
    "14. Output ONLY the code file — no markdown fences, no explanations, "
    "no comments about what to add later",
)

RAILS_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
class User < ApplicationRecord
  has_secure_password

  has_many :products, dependent: :destroy

  validates :email, presence: true,
                    uniqueness: { case_sensitive: false },
                    format: { with: URI::MailTo::EMAIL_REGEXP }
  validates :full_name, presence: true, length: { maximum: 255 }
  validates :password, length: { minimum: 8 }, if: :password_digest_changed?

  scope :active, -> { where(is_active: true) }
  scope :recent, -> { order(created_at: :desc) }

  before_save :downcase_email

  def generate_jwt
    payload = {
      sub: id,
      email: email,
      exp: 24.hours.from_now.to_i,
      iat: Time.current.to_i
    }
    JWT.encode(payload, Rails.application.credentials.secret_key_base, "HS256")
  end

  private

  def downcase_email
    self.email = email.downcase
  end
end


class Product < ApplicationRecord
  belongs_to :owner, class_name: "User"

  validates :name, presence: true, length: { maximum: 255 }
  validates :description, length: { maximum: 5000 }, allow_blank: true
  validates :price, presence: true, numericality: { greater_than: 0 }

  scope :by_owner, ->(user_id) { where(owner_id: user_id) }
  scope :recent, -> { order(created_at: :desc) }
end
''',
    "controllers": '''\
module Api
  module V1
    class UsersController < ApplicationController
      skip_before_action :authenticate_user!, only: [:create]

      def index
        users = User.active.recent
        render json: users, each_serializer: UserSerializer, status: :ok
      end

      def show
        user = User.find(params[:id])
        render json: user, serializer: UserSerializer, status: :ok
      end

      def create
        user = User.new(user_params)

        if user.save
          token = user.generate_jwt
          render json: { user: UserSerializer.new(user), token: token }, status: :created
        else
          render json: { errors: user.errors.full_messages }, status: :unprocessable_entity
        end
      end

      private

      def user_params
        params.require(:user).permit(:email, :full_name, :password, :password_confirmation)
      end
    end
  end
end


module Api
  module V1
    class ProductsController < ApplicationController
      before_action :set_product, only: [:show, :update, :destroy]

      def index
        products = current_user.products.recent
        render json: products, each_serializer: ProductSerializer, status: :ok
      end

      def show
        render json: @product, serializer: ProductSerializer, status: :ok
      end

      def create
        product = current_user.products.build(product_params)

        if product.save
          render json: product, serializer: ProductSerializer, status: :created
        else
          render json: { errors: product.errors.full_messages }, status: :unprocessable_entity
        end
      end

      def update
        if @product.update(product_params)
          render json: @product, serializer: ProductSerializer, status: :ok
        else
          render json: { errors: @product.errors.full_messages }, status: :unprocessable_entity
        end
      end

      def destroy
        @product.destroy!
        head :no_content
      end

      private

      def set_product
        @product = current_user.products.find(params[:id])
      end

      def product_params
        params.require(:product).permit(:name, :description, :price)
      end
    end
  end
end
''',
    "serializers": '''\
class UserSerializer < ActiveModel::Serializer
  attributes :id, :email, :full_name, :is_active, :created_at

  has_many :products, serializer: ProductSerializer
end


class ProductSerializer < ActiveModel::Serializer
  attributes :id, :name, :description, :price, :created_at, :updated_at

  belongs_to :owner, serializer: UserSerializer

  def price
    object.price.to_f
  end
end
''',
    "migrations": '''\
class CreateUsers < ActiveRecord::Migration[7.1]
  def change
    create_table :users do |t|
      t.string :email, null: false, limit: 255
      t.string :full_name, null: false, limit: 255
      t.string :password_digest, null: false
      t.boolean :is_active, null: false, default: true

      t.timestamps
    end

    add_index :users, :email, unique: true
    add_index :users, :created_at
  end
end


class CreateProducts < ActiveRecord::Migration[7.1]
  def change
    create_table :products do |t|
      t.string :name, null: false, limit: 255
      t.text :description
      t.decimal :price, null: false, precision: 10, scale: 2
      t.references :owner, null: false, foreign_key: { to_table: :users }

      t.timestamps
    end

    add_index :products, :created_at
  end
end
''',
}

RAILS_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/app/models/",
    "controllers": "backend/app/controllers/api/v1/",
    "serializers": "backend/app/serializers/",
    "concerns": "backend/app/models/concerns/",
    "controller_concerns": "backend/app/controllers/concerns/",
    "migrations": "backend/db/migrate/",
    "routes": "backend/config/routes.rb",
    "services": "backend/app/services/",
    "tests": "backend/spec/",
    "seed": "backend/db/seeds.rb",
    "config": "backend/config/application.rb",
    "initializers": "backend/config/initializers/",
}


RAILS_CONFIG = FrameworkConfig(
    name="rails",
    display_name="Ruby on Rails",
    language="ruby",
    code_block_lang="ruby",
    error_comment_prefix="#",
    file_structure=RAILS_FILE_STRUCTURE,
    rules=RAILS_RULES,
    golden_examples=RAILS_GOLDEN_EXAMPLES,
)

register_framework(RAILS_CONFIG)
