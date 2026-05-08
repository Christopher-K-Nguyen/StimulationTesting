function varargout = setArduinoVoltage(File,varargin)
%% Variables
bias = File.Parameters.Bias;
isQuit = false;
if bias > 0
    if ~isempty(varargin)
        returnOCP = varargin{1};
    else
        returnOCP = File.Parameters.CounterElectrode.OpenCircuitPotential;
    end
    arduinoDevice = File.Arduino.Object;

    %% Function
    fprintf('Setting Arduino voltage...');
    startTime = tic;
    % Set voltage
    if checkTolerance(bias,returnOCP,0.03)
        bias_diff = File.Arduino.Bias;
        change = returnOCP - bias;
        bias_diff = bias_diff - change;
    else
        bias_diff = bias - returnOCP;  % Default voltage setpoint
    end
    fprintf('%.3f V to ',bias_diff);
    [wiper,biasDiff_round] = getWiper(bias_diff);
    wiper_use = sprintf('%f',wiper);
    writeline(arduinoDevice,wiper_use);

    [endTime,unit] = getEndTime(startTime);
    printf('%.3f V (Wiper Position: %d) (%.2f %s)\n', ...
        biasDiff_round,wiper,endTime,unit);

    %% Store
    File.Arduino.Bias = bias_diff;
end

varargout{1} = File;
varargout{2} = isQuit;

end