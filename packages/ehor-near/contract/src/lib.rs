use near_sdk::json_types::U64;
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{
    assert_one_yocto, env, near, require, AccountId, Gas, NearToken, Promise, PromiseError,
};

const PAYLOAD_DOMAIN: &str = "ehor-escrow/v1";
const KEY_PAYLOAD_DOMAIN: &str = "ehor-escrow/key/v1";
const EVENT_STANDARD: &str = "ehor-escrow";
const EVENT_VERSION: &str = "1.0.0";
const MAX_MESSAGE_ID_LEN: usize = 128;
const MAX_CLAIMANTS: usize = 8;
const MAX_PAGE: u64 = 50;
const MAX_ENCRYPTION_KEYS: usize = 8;
const CALLBACK_GAS: Gas = Gas::from_tgas(10);

#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Open,
    Claimed,
    Done,
    Releasing,
    Released,
    Cancelled,
    Refunded,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Account {
    pub balance: NearToken,
    pub device_key: String,
    pub max_per_bounty: NearToken,
}

impl Account {
    fn credit(&mut self, amount: NearToken) {
        self.balance = self.balance.saturating_add(amount);
    }

    fn debit(&mut self, amount: NearToken) {
        self.balance = required(self.balance.checked_sub(amount), "ERR_INSUFFICIENT_BALANCE");
    }
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Bounty {
    pub id: u64,
    pub sender: AccountId,
    pub amount: NearToken,
    pub message_id: String,
    pub allowed_claimants: Vec<AccountId>,
    pub claimant: Option<AccountId>,
    pub status: Status,
    pub deadline: U64,
    pub created: U64,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, Default)]
pub struct EncryptionKeys {
    pub nonce: u64,
    pub keys: Vec<String>,
}

#[near(contract_state)]
pub struct Contract {
    accounts: LookupMap<AccountId, Account>,
    bounties: LookupMap<u64, Bounty>,
    next_bounty_id: u64,
}

impl Default for Contract {
    fn default() -> Self {
        Self {
            accounts: LookupMap::new(b"a"),
            bounties: LookupMap::new(b"b"),
            next_bounty_id: 1,
        }
    }
}

pub fn release_payload(
    chain_id: &str,
    contract: &AccountId,
    bounty_id: u64,
    claimant: &AccountId,
    amount: NearToken,
) -> String {
    format!(
        "{PAYLOAD_DOMAIN}|{chain_id}|{contract}|{bounty_id}|{claimant}|{}",
        amount.as_yoctonear()
    )
}

pub fn encryption_key_payload(
    chain_id: &str,
    contract: &AccountId,
    account: &AccountId,
    nonce: u64,
    action: &str,
    key: &str,
) -> String {
    format!("{KEY_PAYLOAD_DOMAIN}|{chain_id}|{contract}|{account}|{nonce}|{action}|{key}")
}

// Kept outside the contract state struct so the deployed contract upgrades without a migration.
fn encryption_keys() -> LookupMap<AccountId, EncryptionKeys> {
    LookupMap::new(b"e")
}

fn required<T>(value: Option<T>, error: &str) -> T {
    value.unwrap_or_else(|| env::panic_str(error))
}

fn decode_fixed<const N: usize>(value: &str, error: &str) -> [u8; N] {
    required(
        hex::decode(value)
            .ok()
            .and_then(|bytes| <[u8; N]>::try_from(bytes).ok()),
        error,
    )
}

fn normalize_device_key(value: &str) -> String {
    let key: [u8; 33] = decode_fixed(value, "ERR_BAD_DEVICE_KEY");
    require!(key[0] == 2 || key[0] == 3, "ERR_BAD_DEVICE_KEY");
    hex::encode(key)
}

fn normalize_encryption_key(value: &str) -> String {
    let key: [u8; 32] = decode_fixed(value, "ERR_BAD_ENCRYPTION_KEY");
    hex::encode(key)
}

fn verify_device_signature(device_key: &str, signature: &str, payload: &str) {
    let signature: [u8; 64] = decode_fixed(signature, "ERR_BAD_SIGNATURE");
    // Rejecting high-S keeps one valid encoding per signature, as ehor normalizes to low-S.
    require!(env::p256_signature_is_low_s(&signature), "ERR_HIGH_S");
    let device_key: [u8; 33] = decode_fixed(device_key, "ERR_BAD_DEVICE_KEY");
    let digest = env::sha256_array(payload.as_bytes());
    require!(
        env::p256_verify(&signature, &digest, &device_key),
        "ERR_BAD_SIGNATURE"
    );
}

// A function-call key cannot attach NEAR, so an attached deposit or yoctoNEAR proves the
// caller signed with a full-access key.
fn required_deposit() -> NearToken {
    let deposit = env::attached_deposit();
    require!(!deposit.is_zero(), "ERR_DEPOSIT_REQUIRED");
    deposit
}

fn full_access_caller() -> AccountId {
    assert_one_yocto();
    env::predecessor_account_id()
}

fn emit(event: &str, data: Value) {
    let body = json!({
        "standard": EVENT_STANDARD,
        "version": EVENT_VERSION,
        "event": event,
        "data": [data],
    });
    env::log_str(&format!("EVENT_JSON:{body}"));
}

fn emit_amount(event: &str, account: &AccountId, amount: NearToken) {
    emit(event, json!({"account": account, "amount": amount}));
}

fn emit_claimant(event: &str, bounty_id: u64, claimant: &AccountId) {
    emit(event, json!({"bounty_id": bounty_id, "claimant": claimant}));
}

#[near]
impl Contract {
    #[payable]
    pub fn register(&mut self, p256_public_key: String, max_per_bounty: NearToken) {
        let owner = env::predecessor_account_id();
        require!(
            !self.accounts.contains_key(&owner),
            "ERR_ALREADY_REGISTERED"
        );
        let deposit = required_deposit();
        let device_key = normalize_device_key(&p256_public_key);
        emit(
            "register",
            json!({"account": owner, "device_key": device_key, "balance": deposit}),
        );
        self.accounts.insert(
            owner,
            Account {
                balance: deposit,
                device_key,
                max_per_bounty,
            },
        );
    }

    #[payable]
    pub fn deposit(&mut self) {
        let owner = env::predecessor_account_id();
        let deposit = required_deposit();
        self.account_mut(&owner).credit(deposit);
        emit_amount("deposit", &owner, deposit);
    }

    #[payable]
    pub fn withdraw(&mut self, amount: NearToken) -> Promise {
        let owner = full_access_caller();
        require!(!amount.is_zero(), "ERR_ZERO_AMOUNT");
        self.account_mut(&owner).debit(amount);
        emit_amount("withdraw", &owner, amount);
        Promise::new(owner.clone())
            .transfer(amount)
            .then(Self::callback().on_withdraw(owner, amount))
    }

    #[payable]
    pub fn set_device_key(&mut self, p256_public_key: String) {
        let owner = full_access_caller();
        let device_key = normalize_device_key(&p256_public_key);
        self.account_mut(&owner).device_key = device_key.clone();
        emit(
            "set_device_key",
            json!({"account": owner, "device_key": device_key}),
        );
    }

    #[payable]
    pub fn set_max_per_bounty(&mut self, amount: NearToken) {
        let owner = full_access_caller();
        self.account_mut(&owner).max_per_bounty = amount;
        emit_amount("set_max_per_bounty", &owner, amount);
    }

    /// Approves an X25519 key that teammates may encrypt ehor messages to. Needs the owner's
    /// full-access key and a signature from the registered device key over the key payload.
    #[payable]
    pub fn add_encryption_key(&mut self, x25519_public_key: String, signature: String) {
        let owner = full_access_caller();
        let key = normalize_encryption_key(&x25519_public_key);
        let device_key = self.account_ref(&owner).device_key.clone();
        let mut stored = encryption_keys();
        let mut record = stored.get(&owner).cloned().unwrap_or_default();
        require!(!record.keys.contains(&key), "ERR_DUPLICATE_KEY");
        require!(record.keys.len() < MAX_ENCRYPTION_KEYS, "ERR_TOO_MANY_KEYS");
        Self::verify_key_change(&owner, &device_key, &record, "add", &key, &signature);
        record.nonce += 1;
        record.keys.push(key.clone());
        emit("add_encryption_key", json!({"account": owner, "key": key}));
        stored.insert(owner, record);
    }

    #[payable]
    pub fn remove_encryption_key(&mut self, x25519_public_key: String, signature: String) {
        let owner = full_access_caller();
        let key = normalize_encryption_key(&x25519_public_key);
        let device_key = self.account_ref(&owner).device_key.clone();
        let mut stored = encryption_keys();
        let mut record = stored.get(&owner).cloned().unwrap_or_default();
        require!(record.keys.contains(&key), "ERR_UNKNOWN_KEY");
        Self::verify_key_change(&owner, &device_key, &record, "remove", &key, &signature);
        record.nonce += 1;
        record.keys.retain(|stored_key| stored_key != &key);
        emit(
            "remove_encryption_key",
            json!({"account": owner, "key": key}),
        );
        stored.insert(owner, record);
    }

    pub fn post_bounty(
        &mut self,
        amount: NearToken,
        message_id: String,
        allowed_claimants: Vec<AccountId>,
        deadline: U64,
    ) -> u64 {
        let sender = env::predecessor_account_id();
        let now = env::block_timestamp();
        require!(!amount.is_zero(), "ERR_ZERO_AMOUNT");
        require!(
            !message_id.is_empty() && message_id.len() <= MAX_MESSAGE_ID_LEN,
            "ERR_BAD_MESSAGE_ID"
        );
        require!(
            !allowed_claimants.is_empty() && allowed_claimants.len() <= MAX_CLAIMANTS,
            "ERR_BAD_CLAIMANTS"
        );
        require!(
            !allowed_claimants.contains(&sender),
            "ERR_SENDER_IS_CLAIMANT"
        );
        require!(deadline.0 > now, "ERR_DEADLINE_PASSED");

        let account = self.account_mut(&sender);
        require!(amount <= account.max_per_bounty, "ERR_OVER_LIMIT");
        account.debit(amount);

        let id = self.next_bounty_id;
        self.next_bounty_id += 1;
        emit(
            "post_bounty",
            json!({
                "bounty_id": id,
                "sender": sender,
                "amount": amount,
                "message_id": message_id,
                "deadline": deadline,
            }),
        );
        self.bounties.insert(
            id,
            Bounty {
                id,
                sender,
                amount,
                message_id,
                allowed_claimants,
                claimant: None,
                status: Status::Open,
                deadline,
                created: U64(now),
            },
        );
        id
    }

    pub fn claim(&mut self, bounty_id: u64) {
        let caller = env::predecessor_account_id();
        let now = env::block_timestamp();
        let bounty = self.bounty_mut(bounty_id);
        require!(bounty.status == Status::Open, "ERR_NOT_OPEN");
        require!(now < bounty.deadline.0, "ERR_DEADLINE_PASSED");
        require!(
            bounty.allowed_claimants.contains(&caller),
            "ERR_NOT_ALLOWED"
        );
        bounty.claimant = Some(caller.clone());
        bounty.status = Status::Claimed;
        emit_claimant("claim", bounty_id, &caller);
    }

    pub fn mark_done(&mut self, bounty_id: u64) {
        let caller = env::predecessor_account_id();
        let bounty = self.bounty_mut(bounty_id);
        require!(bounty.status == Status::Claimed, "ERR_NOT_CLAIMED");
        require!(
            bounty.claimant.as_ref() == Some(&caller),
            "ERR_NOT_CLAIMANT"
        );
        bounty.status = Status::Done;
        emit_claimant("mark_done", bounty_id, &caller);
    }

    pub fn release(&mut self, bounty_id: u64, signature: String) -> Promise {
        let (sender, claimant, amount) = {
            let bounty = self.bounty_ref(bounty_id);
            require!(bounty.status == Status::Done, "ERR_NOT_DONE");
            let claimant = required(bounty.claimant.clone(), "ERR_NO_CLAIMANT");
            (bounty.sender.clone(), claimant, bounty.amount)
        };

        let payload = release_payload(
            &env::chain_id(),
            &env::current_account_id(),
            bounty_id,
            &claimant,
            amount,
        );
        verify_device_signature(&self.account_ref(&sender).device_key, &signature, &payload);

        // The status changes before the transfer so a second release in the next block
        // cannot pay twice; the callback reverts it if the transfer fails.
        self.bounty_mut(bounty_id).status = Status::Releasing;
        Promise::new(claimant)
            .transfer(amount)
            .then(Self::callback().on_release(bounty_id))
    }

    pub fn cancel(&mut self, bounty_id: u64) {
        self.close(bounty_id, Status::Cancelled, "cancel", |bounty| {
            require!(bounty.status == Status::Open, "ERR_NOT_OPEN");
        });
    }

    pub fn refund(&mut self, bounty_id: u64) {
        let now = env::block_timestamp();
        self.close(bounty_id, Status::Refunded, "refund", |bounty| {
            require!(
                matches!(bounty.status, Status::Open | Status::Claimed),
                "ERR_NOT_REFUNDABLE"
            );
            require!(now >= bounty.deadline.0, "ERR_DEADLINE_NOT_PASSED");
        });
    }

    #[private]
    pub fn on_release(
        &mut self,
        bounty_id: u64,
        #[callback_result] result: Result<(), PromiseError>,
    ) -> bool {
        let paid = result.is_ok();
        let bounty = self.bounty_mut(bounty_id);
        require!(bounty.status == Status::Releasing, "ERR_NOT_RELEASING");
        bounty.status = if paid { Status::Released } else { Status::Done };
        emit(
            if paid { "release" } else { "release_failed" },
            json!({
                "bounty_id": bounty_id,
                "claimant": bounty.claimant,
                "amount": bounty.amount,
            }),
        );
        paid
    }

    #[private]
    pub fn on_withdraw(
        &mut self,
        account_id: AccountId,
        amount: NearToken,
        #[callback_result] result: Result<(), PromiseError>,
    ) -> bool {
        let paid = result.is_ok();
        if !paid {
            self.account_mut(&account_id).credit(amount);
            emit_amount("withdraw_failed", &account_id, amount);
        }
        paid
    }

    pub fn get_account(&self, account_id: AccountId) -> Option<Account> {
        self.accounts.get(&account_id).cloned()
    }

    pub fn get_encryption_keys(&self, account_id: AccountId) -> EncryptionKeys {
        encryption_keys()
            .get(&account_id)
            .cloned()
            .unwrap_or_default()
    }

    pub fn get_bounty(&self, bounty_id: u64) -> Option<Bounty> {
        self.bounties.get(&bounty_id).cloned()
    }

    pub fn get_bounties_by_sender(
        &self,
        sender: AccountId,
        from_index: Option<u64>,
        limit: Option<u64>,
    ) -> Vec<Bounty> {
        let start = from_index.unwrap_or(1).max(1);
        let end = start
            .saturating_add(limit.unwrap_or(MAX_PAGE).min(MAX_PAGE))
            .min(self.next_bounty_id);
        (start..end)
            .filter_map(|id| self.bounties.get(&id))
            .filter(|bounty| bounty.sender == sender)
            .cloned()
            .collect()
    }
}

impl Contract {
    fn close(
        &mut self,
        bounty_id: u64,
        status: Status,
        event: &str,
        allowed: impl FnOnce(&Bounty),
    ) {
        let caller = env::predecessor_account_id();
        let amount = {
            let bounty = self.bounty_mut(bounty_id);
            require!(bounty.sender == caller, "ERR_NOT_SENDER");
            allowed(bounty);
            bounty.status = status;
            bounty.amount
        };
        self.account_mut(&caller).credit(amount);
        emit(event, json!({"bounty_id": bounty_id}));
    }

    // The nonce in the payload makes each signature good for one change, so a signature seen
    // on-chain cannot re-add a key after it was removed.
    fn verify_key_change(
        owner: &AccountId,
        device_key: &str,
        record: &EncryptionKeys,
        action: &str,
        key: &str,
        signature: &str,
    ) {
        let payload = encryption_key_payload(
            &env::chain_id(),
            &env::current_account_id(),
            owner,
            record.nonce,
            action,
            key,
        );
        verify_device_signature(device_key, signature, &payload);
    }

    fn callback() -> ContractExt {
        Self::ext(env::current_account_id()).with_static_gas(CALLBACK_GAS)
    }

    fn account_ref(&self, account_id: &AccountId) -> &Account {
        required(self.accounts.get(account_id), "ERR_NOT_REGISTERED")
    }

    fn account_mut(&mut self, account_id: &AccountId) -> &mut Account {
        required(self.accounts.get_mut(account_id), "ERR_NOT_REGISTERED")
    }

    fn bounty_ref(&self, bounty_id: u64) -> &Bounty {
        required(self.bounties.get(&bounty_id), "ERR_NO_BOUNTY")
    }

    fn bounty_mut(&mut self, bounty_id: u64) -> &mut Bounty {
        required(self.bounties.get_mut(&bounty_id), "ERR_NO_BOUNTY")
    }
}

#[cfg(test)]
mod tests;
