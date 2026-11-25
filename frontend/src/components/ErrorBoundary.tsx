import { Component, type ReactNode } from "react";
import GenericError from "../pages/errors/GenericError";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Catches uncaught render errors anywhere below it and shows GenericError
 * instead of a blank white screen. Mounted once around <App/> in main.tsx. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("Uncaught render error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <GenericError
          message={this.state.error.message}
          onRetry={() => this.setState({ error: null })}
        />
      );
    }
    return this.props.children;
  }
}
