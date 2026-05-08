function [s,varargout] = rmfield2(s,field)
%% Variables
field_cell = fieldnames(s);
isChanged = false;

%% Change
if contains2(field_cell,field)
    s = rmfield(s,field);
    isChanged = true;
end

%% Output
numOfOutputs = nargout;
varargout = cell(1,numOfOutputs);
for output_idx = 1:numOfOutputs
      varargout{output_idx} = isChanged;
end

end