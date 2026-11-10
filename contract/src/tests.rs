use near_sdk::json_types::U64;
use near_sdk::serde_json::{self, Value};
use near_sdk::test_utils::VMContextBuilder;
use near_sdk::{env, testing_env, AccountId, NearToken, PromiseError};
use p256::ecdsa::signature::hazmat::PrehashSigner;
use p256::ecdsa::{Signature, SigningKey};

use crate::{release_payload, Contract, Status};

const DEADLINE: u64 = 1_000;
const NEAR: NearToken = NearToken::from_near(1);
const YOCTO: NearToken = NearToken::from_yoctonear(1);
const ZERO: NearToken = NearToken::from_yoctonear(0);
const RELEASE_VECTOR: &str = include_str!("../../docs/vectors/release-v1.json");

fn id(name: &str) -> AccountId {
    name.parse().unwrap()
}

fn escrow() -> AccountId {
    id("escrow.testnet")
}

fn ada() -> AccountId {
    id("ada.testnet")
}

fn tobi() -> AccountId {
    id("tobi.testnet")
}

fn mallory() -> AccountId {
    id("mallory.testnet")
}

fn call_as(caller: &AccountId, deposit: NearToken, now: u64) {
    testing_env!(VMContextBuilder::new()
        .current_account_id(escrow())
        .predecessor_account_id(caller.clone())
        .signer_account_id(caller.clone())
        .attached_deposit(deposit)
        .block_timestamp(now)
        .build());
}

fn device_key(seed: u8) -> SigningKey {
    SigningKey::from_slice(&[seed; 32]).unwrap()
}

fn public_hex(key: &SigningKey) -> String {
    hex::encode(key.verifying_key().to_encoded_point(true).as_bytes())
}

fn sign_raw(key: &SigningKey, payload: &str) -> Signature {
    let digest = env::sha256_array(payload.as_bytes());
    let signature: Signature = key.sign_prehash(&digest).unwrap();
    signature.normalize_s().unwrap_or(signature)
}

fn sign(key: &SigningKey, payload: &str) -> String {
    hex::encode(sign_raw(key, payload).to_bytes())
}

fn payload_for(contract: &Contract, bounty_id: u64) -> String {
    let bounty = contract.get_bounty(bounty_id).unwrap();
    release_payload(
        &env::chain_id(),
        &escrow(),
        bounty_id,
        &bounty.claimant.unwrap(),
        bounty.amount,
    )
}

fn registered() -> Contract {
    let mut contract = Contract::default();
    call_as(&ada(), NearToken::from_near(10), 0);
    contract.register(public_hex(&device_key(7)), NearToken::from_near(5));
    contract
}

fn posted() -> (Contract, u64) {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    let bounty_id = contract.post_bounty(NEAR, "msg-1".into(), vec![tobi()], U64(DEADLINE));
    (contract, bounty_id)
}

fn claimed() -> (Contract, u64) {
    let (mut contract, bounty_id) = posted();
    call_as(&tobi(), ZERO, 1);
    contract.claim(bounty_id);
    (contract, bounty_id)
}

fn done() -> (Contract, u64) {
    let (mut contract, bounty_id) = claimed();
    call_as(&tobi(), ZERO, 2);
    contract.mark_done(bounty_id);
    (contract, bounty_id)
}

fn releasing() -> (Contract, u64) {
    let (mut contract, bounty_id) = done();
    call_as(&mallory(), ZERO, 3);
    let signature = sign(&device_key(7), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
    (contract, bounty_id)
}

fn status(contract: &Contract, bounty_id: u64) -> Status {
    contract.get_bounty(bounty_id).unwrap().status
}

fn balance(contract: &Contract) -> NearToken {
    contract.get_account(ada()).unwrap().balance
}

#[test]
fn payload_matches_shared_vector() {
    let vector: Value = serde_json::from_str(RELEASE_VECTOR).unwrap();
    let field = |name: &str| vector[name].as_str().unwrap().to_owned();
    let amount = NearToken::from_yoctonear(field("amount_yocto").parse().unwrap());
    let payload = release_payload(
        &field("chain_id"),
        &id(&field("contract")),
        vector["bounty_id"].as_u64().unwrap(),
        &id(&field("claimant")),
        amount,
    );
    assert_eq!(payload, field("payload"));
    assert_eq!(
        hex::encode(env::sha256_array(payload.as_bytes())),
        field("sha256")
    );
}

#[test]
fn register_credits_deposit() {
    let contract = registered();
    let account = contract.get_account(ada()).unwrap();
    assert_eq!(account.balance, NearToken::from_near(10));
    assert_eq!(account.device_key, public_hex(&device_key(7)));
    assert_eq!(account.max_per_bounty, NearToken::from_near(5));
}

#[test]
#[should_panic(expected = "ERR_DEPOSIT_REQUIRED")]
fn register_without_deposit_fails() {
    let mut contract = Contract::default();
    call_as(&ada(), ZERO, 0);
    contract.register(public_hex(&device_key(7)), NEAR);
}

#[test]
#[should_panic(expected = "ERR_ALREADY_REGISTERED")]
fn register_twice_fails() {
    let mut contract = registered();
    call_as(&ada(), NEAR, 0);
    contract.register(public_hex(&device_key(8)), NEAR);
}

#[test]
#[should_panic(expected = "ERR_BAD_DEVICE_KEY")]
fn register_rejects_malformed_key() {
    let mut contract = Contract::default();
    call_as(&ada(), NEAR, 0);
    contract.register("04".repeat(33), NEAR);
}

#[test]
fn deposit_adds_to_balance() {
    let mut contract = registered();
    call_as(&ada(), NEAR, 0);
    contract.deposit();
    assert_eq!(balance(&contract), NearToken::from_near(11));
}

#[test]
#[should_panic(expected = "ERR_NOT_REGISTERED")]
fn deposit_requires_registration() {
    let mut contract = Contract::default();
    call_as(&mallory(), NEAR, 0);
    contract.deposit();
}

#[test]
fn withdraw_debits_balance() {
    let mut contract = registered();
    call_as(&ada(), YOCTO, 0);
    let _ = contract.withdraw(NearToken::from_near(4));
    assert_eq!(balance(&contract), NearToken::from_near(6));
}

#[test]
#[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
fn withdraw_with_agent_key_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    let _ = contract.withdraw(NEAR);
}

#[test]
#[should_panic(expected = "ERR_INSUFFICIENT_BALANCE")]
fn withdraw_over_balance_fails() {
    let mut contract = registered();
    call_as(&ada(), YOCTO, 0);
    let _ = contract.withdraw(NearToken::from_near(11));
}

#[test]
fn failed_withdraw_is_credited_back() {
    let mut contract = registered();
    call_as(&ada(), YOCTO, 0);
    let _ = contract.withdraw(NEAR);
    call_as(&escrow(), ZERO, 0);
    assert!(!contract.on_withdraw(ada(), NEAR, Err(PromiseError::Failed)));
    assert_eq!(balance(&contract), NearToken::from_near(10));
}

#[test]
#[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
fn set_device_key_with_agent_key_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.set_device_key(public_hex(&device_key(9)));
}

#[test]
#[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
fn set_max_per_bounty_with_agent_key_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.set_max_per_bounty(NearToken::from_near(100));
}

#[test]
fn post_moves_budget_into_bounty() {
    let (contract, bounty_id) = posted();
    let bounty = contract.get_bounty(bounty_id).unwrap();
    assert_eq!(bounty_id, 1);
    assert_eq!(bounty.status, Status::Open);
    assert_eq!(bounty.amount, NEAR);
    assert_eq!(bounty.message_id, "msg-1");
    assert_eq!(balance(&contract), NearToken::from_near(9));
}

#[test]
#[should_panic(expected = "ERR_NOT_REGISTERED")]
fn post_requires_registration() {
    let mut contract = registered();
    call_as(&mallory(), ZERO, 0);
    contract.post_bounty(NEAR, "msg-1".into(), vec![tobi()], U64(DEADLINE));
}

#[test]
#[should_panic(expected = "ERR_OVER_LIMIT")]
fn post_over_limit_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(
        NearToken::from_near(6),
        "msg-1".into(),
        vec![tobi()],
        U64(DEADLINE),
    );
}

#[test]
#[should_panic(expected = "ERR_INSUFFICIENT_BALANCE")]
fn post_over_balance_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    for index in 0..3 {
        contract.post_bounty(
            NearToken::from_near(4),
            format!("msg-{index}"),
            vec![tobi()],
            U64(DEADLINE),
        );
    }
}

#[test]
#[should_panic(expected = "ERR_ZERO_AMOUNT")]
fn post_zero_amount_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(ZERO, "msg-1".into(), vec![tobi()], U64(DEADLINE));
}

#[test]
#[should_panic(expected = "ERR_DEADLINE_PASSED")]
fn post_with_past_deadline_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, DEADLINE);
    contract.post_bounty(NEAR, "msg-1".into(), vec![tobi()], U64(DEADLINE));
}

#[test]
#[should_panic(expected = "ERR_SENDER_IS_CLAIMANT")]
fn post_naming_sender_as_claimant_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(NEAR, "msg-1".into(), vec![ada()], U64(DEADLINE));
}

#[test]
#[should_panic(expected = "ERR_BAD_CLAIMANTS")]
fn post_without_claimants_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(NEAR, "msg-1".into(), vec![], U64(DEADLINE));
}

#[test]
#[should_panic(expected = "ERR_BAD_MESSAGE_ID")]
fn post_with_oversized_message_id_fails() {
    let mut contract = registered();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(NEAR, "m".repeat(129), vec![tobi()], U64(DEADLINE));
}

#[test]
fn claim_sets_claimant() {
    let (contract, bounty_id) = claimed();
    let bounty = contract.get_bounty(bounty_id).unwrap();
    assert_eq!(bounty.status, Status::Claimed);
    assert_eq!(bounty.claimant, Some(tobi()));
}

#[test]
#[should_panic(expected = "ERR_NOT_ALLOWED")]
fn claim_by_unlisted_account_fails() {
    let (mut contract, bounty_id) = posted();
    call_as(&mallory(), ZERO, 1);
    contract.claim(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NOT_OPEN")]
fn double_claim_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&tobi(), ZERO, 2);
    contract.claim(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_DEADLINE_PASSED")]
fn claim_after_deadline_fails() {
    let (mut contract, bounty_id) = posted();
    call_as(&tobi(), ZERO, DEADLINE);
    contract.claim(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NO_BOUNTY")]
fn claim_unknown_bounty_fails() {
    let mut contract = registered();
    call_as(&tobi(), ZERO, 1);
    contract.claim(42);
}

#[test]
#[should_panic(expected = "ERR_NOT_CLAIMANT")]
fn mark_done_by_other_account_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&mallory(), ZERO, 2);
    contract.mark_done(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NOT_CLAIMED")]
fn mark_done_before_claim_fails() {
    let (mut contract, bounty_id) = posted();
    call_as(&tobi(), ZERO, 2);
    contract.mark_done(bounty_id);
}

#[test]
fn release_with_valid_signature_pays_claimant() {
    let (mut contract, bounty_id) = releasing();
    assert_eq!(status(&contract, bounty_id), Status::Releasing);
    call_as(&escrow(), ZERO, 4);
    assert!(contract.on_release(bounty_id, Ok(())));
    assert_eq!(status(&contract, bounty_id), Status::Released);
    assert_eq!(balance(&contract), NearToken::from_near(9));
}

#[test]
fn failed_transfer_returns_bounty_to_done() {
    let (mut contract, bounty_id) = releasing();
    call_as(&escrow(), ZERO, 4);
    assert!(!contract.on_release(bounty_id, Err(PromiseError::Failed)));
    assert_eq!(status(&contract, bounty_id), Status::Done);
}

#[test]
#[should_panic(expected = "ERR_NOT_DONE")]
fn release_before_done_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&ada(), ZERO, 3);
    let signature = sign(&device_key(7), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
}

#[test]
#[should_panic(expected = "ERR_NOT_DONE")]
fn second_release_while_releasing_fails() {
    let (mut contract, bounty_id) = releasing();
    call_as(&mallory(), ZERO, 4);
    let signature = sign(&device_key(7), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
}

#[test]
#[should_panic(expected = "ERR_NOT_DONE")]
fn release_after_released_fails() {
    let (mut contract, bounty_id) = releasing();
    call_as(&escrow(), ZERO, 4);
    contract.on_release(bounty_id, Ok(()));
    call_as(&mallory(), ZERO, 5);
    let signature = sign(&device_key(7), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_signed_by_other_key_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let signature = sign(&device_key(8), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_signature_for_other_bounty_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let payload = release_payload(&env::chain_id(), &escrow(), bounty_id + 1, &tobi(), NEAR);
    let _ = contract.release(bounty_id, sign(&device_key(7), &payload));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_signature_for_other_claimant_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let payload = release_payload(&env::chain_id(), &escrow(), bounty_id, &mallory(), NEAR);
    let _ = contract.release(bounty_id, sign(&device_key(7), &payload));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_signature_for_other_amount_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let payload = release_payload(
        &env::chain_id(),
        &escrow(),
        bounty_id,
        &tobi(),
        NearToken::from_near(2),
    );
    let _ = contract.release(bounty_id, sign(&device_key(7), &payload));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_signature_for_other_contract_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let payload = release_payload(
        &env::chain_id(),
        &id("other.testnet"),
        bounty_id,
        &tobi(),
        NEAR,
    );
    let _ = contract.release(bounty_id, sign(&device_key(7), &payload));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_signature_for_other_chain_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let payload = release_payload("not-this-chain", &escrow(), bounty_id, &tobi(), NEAR);
    let _ = contract.release(bounty_id, sign(&device_key(7), &payload));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_with_malformed_signature_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let _ = contract.release(bounty_id, "abcd".into());
}

#[test]
#[should_panic(expected = "ERR_HIGH_S")]
fn release_with_high_s_signature_fails() {
    const ORDER: [u8; 32] = [
        0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63,
        0x25, 0x51,
    ];
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, 3);
    let mut bytes = sign_raw(&device_key(7), &payload_for(&contract, bounty_id)).to_bytes();
    let mut borrow = 0i16;
    for index in (0..32).rev() {
        let difference = i16::from(ORDER[index]) - i16::from(bytes[32 + index]) - borrow;
        borrow = i16::from(difference < 0);
        bytes[32 + index] = (difference + borrow * 256) as u8;
    }
    let _ = contract.release(bounty_id, hex::encode(bytes));
}

#[test]
#[should_panic(expected = "ERR_BAD_SIGNATURE")]
fn release_after_device_key_rotation_rejects_old_key() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), YOCTO, 3);
    contract.set_device_key(public_hex(&device_key(9)));
    call_as(&ada(), ZERO, 4);
    let signature = sign(&device_key(7), &payload_for(&contract, bounty_id));
    let _ = contract.release(bounty_id, signature);
}

#[test]
fn cancel_returns_budget() {
    let (mut contract, bounty_id) = posted();
    call_as(&ada(), ZERO, 1);
    contract.cancel(bounty_id);
    assert_eq!(status(&contract, bounty_id), Status::Cancelled);
    assert_eq!(balance(&contract), NearToken::from_near(10));
}

#[test]
#[should_panic(expected = "ERR_NOT_OPEN")]
fn cancel_after_claim_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&ada(), ZERO, 2);
    contract.cancel(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NOT_SENDER")]
fn cancel_by_other_account_fails() {
    let (mut contract, bounty_id) = posted();
    call_as(&mallory(), ZERO, 1);
    contract.cancel(bounty_id);
}

#[test]
fn refund_after_deadline_returns_budget() {
    let (mut contract, bounty_id) = claimed();
    call_as(&ada(), ZERO, DEADLINE);
    contract.refund(bounty_id);
    assert_eq!(status(&contract, bounty_id), Status::Refunded);
    assert_eq!(balance(&contract), NearToken::from_near(10));
}

#[test]
#[should_panic(expected = "ERR_DEADLINE_NOT_PASSED")]
fn refund_before_deadline_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&ada(), ZERO, DEADLINE - 1);
    contract.refund(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NOT_REFUNDABLE")]
fn refund_while_done_fails() {
    let (mut contract, bounty_id) = done();
    call_as(&ada(), ZERO, DEADLINE);
    contract.refund(bounty_id);
}

#[test]
#[should_panic(expected = "ERR_NOT_SENDER")]
fn refund_by_other_account_fails() {
    let (mut contract, bounty_id) = claimed();
    call_as(&mallory(), ZERO, DEADLINE);
    contract.refund(bounty_id);
}

#[test]
fn bounties_are_listed_by_sender() {
    let (mut contract, _) = posted();
    call_as(&ada(), ZERO, 0);
    contract.post_bounty(NEAR, "msg-2".into(), vec![tobi()], U64(DEADLINE));
    assert_eq!(contract.get_bounties_by_sender(ada(), None, None).len(), 2);
    assert_eq!(
        contract.get_bounties_by_sender(ada(), Some(2), None).len(),
        1
    );
    assert!(contract
        .get_bounties_by_sender(tobi(), None, None)
        .is_empty());
}
