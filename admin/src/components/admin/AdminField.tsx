import { Children, cloneElement, isValidElement, useId, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Select, Textarea } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import "@/styles/admin-controls.css";

type AdminFieldProps = {
  label?: string;
  hint?: string;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
  required?: boolean;
  optional?: boolean;
  error?: string;
};

type ControlProps = {
  id?: string; children?: ReactNode; required?: boolean;
  "aria-describedby"?: string; "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling";
};

// Associate the first field control, including controls nested in a unit or secret-input row.
function connectControl(children: ReactNode, attrs: ControlProps): ReactNode {
  let connected = false;
  function visit(nodes: ReactNode): ReactNode {
    return Children.map(nodes, (node) => {
      if (connected || !isValidElement<ControlProps>(node)) return node;
      if (node.type === "input" || node.type === "select" || node.type === "textarea" || node.type === Input || node.type === Select || node.type === Textarea || node.type === Switch) {
        connected = true;
        return cloneElement(node, {
          ...attrs,
          id: attrs.id,
          required: attrs.required || node.props.required,
          "aria-invalid": attrs["aria-invalid"] || node.props["aria-invalid"],
          "aria-describedby": [node.props["aria-describedby"], attrs["aria-describedby"]].filter(Boolean).join(" ") || undefined,
        });
      }
      return node.props.children ? cloneElement(node, { children: visit(node.props.children) }) : node;
    });
  }
  return visit(children);
}

export function AdminField({ label, hint, htmlFor, className, children, required, optional, error }: AdminFieldProps) {
  const generatedId = useId();
  const directControl = Children.toArray(children).find((child) => isValidElement<ControlProps>(child) && child.props.id);
  const controlId = htmlFor ?? (isValidElement<ControlProps>(directControl) ? directControl.props.id : undefined) ?? `field-${generatedId}`;
  const hintId = `${generatedId}-hint`;
  const errorId = `${generatedId}-error`;
  return (
    <div className={cn("admin-field admin-form-field", error && "has-error", className)}>
      {label ? <label htmlFor={controlId} className="admin-field-label">{label}{required ? <span className="admin-field-required">必填</span> : optional ? <span className="admin-field-optional">选填</span> : null}</label> : null}
      {connectControl(children, { id: controlId, required, "aria-invalid": error ? true : undefined, "aria-describedby": [hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined })}
      {hint ? <p id={hintId} className="admin-field-hint">{hint}</p> : null}
      {error ? <p id={errorId} role="alert" className="admin-field-error">{error}</p> : null}
    </div>
  );
}
