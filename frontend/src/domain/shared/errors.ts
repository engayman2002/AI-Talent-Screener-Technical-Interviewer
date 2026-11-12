/**
 * Pure TypeScript Domain Errors for Frontend Core Business Policies & State Invariants.
 * Zero framework, DOM, or network dependencies.
 */

export class FrontendDomainError extends Error {
  public readonly code: string;

  public constructor(message: string, code: string) {
    super(message);
    this.name = new.target.name;
    this.code = code;
  }
}

export class InvalidFrontendStateTransitionError extends FrontendDomainError {
  public readonly fromState: string;
  public readonly eventType: string;

  public constructor(
    message: string,
    fromState: string,
    eventType: string,
    code = "INVALID_FRONTEND_STATE_TRANSITION",
  ) {
    super(message, code);
    this.fromState = fromState;
    this.eventType = eventType;
  }
}

export class ValidationDomainError extends FrontendDomainError {
  public constructor(message: string, code = "DOMAIN_VALIDATION_ERROR") {
    super(message, code);
  }
}

export class ComparisonInputError extends FrontendDomainError {
  public constructor(message: string, code = "INVALID_COMPARISON_SELECTION") {
    super(message, code);
  }
}
