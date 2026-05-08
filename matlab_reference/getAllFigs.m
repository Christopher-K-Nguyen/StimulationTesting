function [h,varargout] = getAllFigs()

h =  findobj('type','figure');

numOfOutputs = nargout - 1;
varargout = cell(1,numOfOutputs);
for output_idx = 1:numOfOutputs
      varargout{output_idx} = length(h);
end

end